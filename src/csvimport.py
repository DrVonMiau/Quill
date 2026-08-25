"""Parse an Openreads CSV export into normalized book dicts.

Pure standard-library code — no GTK, no database — so it stays easy to test
headlessly. `parse_openreads()` takes an open text file (or any iterable of
lines) and returns a list of dicts shaped for `library.import_book()`.

Openreads columns:
    title, subtitle, author, description, status, favourite, deleted, rating,
    pages, publication_year, isbn, olid, tags, my_review, notes, book_format,
    readings, date_added, date_modified

Notable field encodings:
- ``status`` is a word: finished / in_progress / planned / abandoned.
- ``rating`` is a 0–5 float ("4.0"); empty means unrated.
- ``readings`` is one or more ``start|finish`` pairs joined by ``;`` (either
  side may be blank), each date an ISO 8601 timestamp.
"""
import csv

# Openreads status -> Quill shelf.
_STATUS_MAP = {
    "finished": "read",
    "read": "read",
    "in_progress": "reading",
    "reading": "reading",
    "planned": "want",
    "want": "want",
    "abandoned": "abandoned",
}


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _norm_tags(value):
    """Normalise a tags cell (comma- or semicolon-separated) to "a, b, c"."""
    value = (value or "").strip()
    if not value:
        return ""
    parts = [p.strip() for p in value.replace(";", ",").split(",")]
    return ", ".join(p for p in parts if p)


def _to_rating(value):
    try:
        return max(0, min(5, round(float(value))))
    except (TypeError, ValueError):
        return 0


def _norm_dt(value):
    """ISO 8601 -> "YYYY-MM-DD HH:MM:SS" (SQLite's datetime() shape), or None."""
    value = (value or "").strip()
    if not value:
        return None
    value = value.replace("T", " ")
    value = value.split(".")[0]  # drop fractional seconds
    date_part = value[:10]
    time_part = value[11:19] if len(value) > 11 else ""
    if len(date_part) != 10:
        return None
    return f"{date_part} {time_part}".strip() if time_part else date_part


def _parse_readings(value):
    """Return (date_started, date_finished) from the readings field, taking the
    earliest start and the latest finish across all recorded readings."""
    value = (value or "").strip()
    if not value:
        return None, None
    starts, finishes = [], []
    for reading in value.split(";"):
        reading = reading.strip()
        if not reading:
            continue
        parts = reading.split("|")
        start = _norm_dt(parts[0]) if len(parts) >= 1 else None
        finish = _norm_dt(parts[1]) if len(parts) >= 2 else None
        if start:
            starts.append(start)
        if finish:
            finishes.append(finish)
    return (min(starts) if starts else None,
            max(finishes) if finishes else None)


def parse_openreads(fileobj):
    """Parse an Openreads CSV. `fileobj` is an open text file or iterable of
    lines. Skips deleted and title-less rows. Returns a list of dicts."""
    reader = csv.DictReader(fileobj)
    books = []
    for row in reader:
        if (row.get("deleted") or "").strip().lower() == "true":
            continue
        title = (row.get("title") or "").strip()
        if not title:
            continue

        status = _STATUS_MAP.get((row.get("status") or "").strip().lower(), "want")
        started, finished = _parse_readings(row.get("readings"))

        notes = (row.get("notes") or "").strip() or (row.get("my_review") or "").strip()

        olid = (row.get("olid") or "").strip().split(",")[0].strip()
        isbn = (row.get("isbn") or "").strip().split(",")[0].strip()

        books.append({
            "title": title,
            "author": (row.get("author") or "").strip(),
            "year": _to_int(row.get("publication_year")),
            "pages": _to_int(row.get("pages")),
            "isbn": isbn,
            "olid": olid,
            "status": status,
            "rating": _to_rating(row.get("rating")),
            "notes": notes,
            "tags": _norm_tags(row.get("tags")),
            "current_page": _to_int(row.get("current_page")),
            "description": (row.get("description") or "").strip(),
            "date_started": started,
            "date_finished": finished,
            "date_added": _norm_dt(row.get("date_added")),
        })
    return books
