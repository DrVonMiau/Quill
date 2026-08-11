"""Reading statistics computed straight from the SQLite library.

Pure standard-library code — no GTK — so the aggregations stay easy to test
headlessly. `compute()` returns a plain dict the Stats view renders.

Finished-date buckets rely on `date_finished` being an ISO-ish string
("YYYY-MM-DD …"), so `substr(date_finished, 1, 4/7)` yields the year/month.
"""
import datetime


def _scalar(con, sql, params=()):
    row = con.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def _totals(con):
    counts = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, COUNT(*) AS n FROM books GROUP BY status")}
    return {
        "library": _scalar(con, "SELECT COUNT(*) FROM books"),
        "read": counts.get("read", 0),
        "reading": counts.get("reading", 0),
        "want": counts.get("want", 0),
        "abandoned": counts.get("abandoned", 0),
        "pages_read": _scalar(
            con, "SELECT COALESCE(SUM(pages), 0) FROM books "
                 "WHERE status='read' AND pages > 0"),
        "avg_rating": round(_scalar(
            con, "SELECT AVG(rating) FROM books WHERE rating > 0"), 1),
        "avg_pages": round(_scalar(
            con, "SELECT AVG(pages) FROM books WHERE status='read' AND pages > 0")),
    }


def _per_year(con):
    rows = con.execute(
        "SELECT substr(date_finished, 1, 4) AS y, COUNT(*) AS n, "
        "COALESCE(SUM(pages), 0) AS pages FROM books "
        "WHERE date_finished IS NOT NULL AND date_finished != '' "
        "GROUP BY y ORDER BY y").fetchall()
    return [(int(r["y"]), r["n"], r["pages"]) for r in rows if r["y"]]


def _per_month(con, months=12):
    """Counts/pages for the last `months` calendar months, ending at the most
    recent finished month (or the current month if the library is empty).
    Zero-filled so the chart shows a continuous timeline."""
    rows = con.execute(
        "SELECT substr(date_finished, 1, 7) AS ym, COUNT(*) AS n, "
        "COALESCE(SUM(pages), 0) AS pages FROM books "
        "WHERE date_finished IS NOT NULL AND date_finished != '' "
        "GROUP BY ym").fetchall()
    data = {r["ym"]: (r["n"], r["pages"]) for r in rows if r["ym"]}
    if data:
        last = max(data)
        end = datetime.date(int(last[:4]), int(last[5:7]), 1)
    else:
        today = datetime.date.today()
        end = datetime.date(today.year, today.month, 1)

    out = []
    year, month = end.year, end.month
    for _ in range(months):
        key = f"{year:04d}-{month:02d}"
        n, pages = data.get(key, (0, 0))
        out.append((key, n, pages))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    out.reverse()
    return out


def _rating_dist(con):
    rows = {r["rating"]: r["n"] for r in con.execute(
        "SELECT rating, COUNT(*) AS n FROM books "
        "WHERE rating BETWEEN 1 AND 5 GROUP BY rating")}
    return [(stars, rows.get(stars, 0)) for stars in range(1, 6)]


def compute(con):
    """Return all Tier-1 reading stats as a dict."""
    return {
        "totals": _totals(con),
        "per_year": _per_year(con),
        "per_month": _per_month(con),
        "rating_dist": _rating_dist(con),
    }
