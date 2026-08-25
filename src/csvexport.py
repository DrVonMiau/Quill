"""Write the Quill library to a CSV.

Pure standard-library code — no GTK, no database handle — so it stays easy to
test headlessly. The columns are an Openreads-compatible superset, so a file
written here reads straight back through ``csvimport.parse_openreads()`` (a
round-trip that also carries tags, reading progress and the summary).
"""
import csv

# Quill shelf -> Openreads status word (mirrors csvimport._STATUS_MAP).
_STATUS_TO_OPENREADS = {
    "read": "finished",
    "reading": "in_progress",
    "want": "planned",
    "abandoned": "abandoned",
}

FIELDNAMES = [
    "title", "author", "publication_year", "pages", "current_page",
    "isbn", "olid", "status", "rating", "tags", "description", "notes",
    "readings", "date_added",
]


def _reading(started, finished):
    """Encode the reading dates as Openreads' ``start|finish`` field."""
    started = (started or "").strip()
    finished = (finished or "").strip()
    if not started and not finished:
        return ""
    return f"{started}|{finished}"


def write_csv(fileobj, rows):
    """Write `rows` (mappings with the Quill book columns) to `fileobj` as CSV.
    Returns the number of books written."""
    writer = csv.DictWriter(fileobj, fieldnames=FIELDNAMES)
    writer.writeheader()
    count = 0
    for r in rows:
        writer.writerow({
            "title": r["title"],
            "author": r["author"] or "",
            "publication_year": r["year"] or "",
            "pages": r["pages"] or "",
            "current_page": r["current_page"] or 0,
            "isbn": r["isbn"] or "",
            "olid": r["olid"] or "",
            "status": _STATUS_TO_OPENREADS.get(r["status"], "planned"),
            "rating": r["rating"] or "",
            "tags": r["tags"] or "",
            "description": r["description"] or "",
            "notes": r["notes"] or "",
            "readings": _reading(r["date_started"], r["date_finished"]),
            "date_added": r["date_added"] or "",
        })
        count += 1
    return count
