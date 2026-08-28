"""SQLite-backed book library. Pure data access — no GTK — so it stays
easy to reason about and unit-test.

The database lives in the XDG data dir and cover images are cached under the
XDG cache dir; both are created on first connect.
"""
import sqlite3
from pathlib import Path

from gi.repository import GLib

DATA_DIR = Path(GLib.get_user_data_dir()) / "quill"
CACHE_DIR = Path(GLib.get_user_cache_dir()) / "quill"
COVERS_DIR = CACHE_DIR / "covers"
DB_PATH = DATA_DIR / "library.db"

# The shelves a book can sit on.
STATUSES = ("want", "reading", "read", "abandoned")
STATUS_LABELS = {
    "want": "Want to read",
    "reading": "Reading",
    "read": "Read",
    "abandoned": "Abandoned",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    olid          TEXT,
    isbn          TEXT,
    title         TEXT NOT NULL,
    author        TEXT DEFAULT '',
    year          INTEGER DEFAULT 0,
    pages         INTEGER DEFAULT 0,
    cover_url     TEXT DEFAULT '',
    cover_path    TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'want',
    rating        INTEGER NOT NULL DEFAULT 0,
    notes         TEXT DEFAULT '',
    description   TEXT DEFAULT '',
    tags          TEXT DEFAULT '',
    current_page  INTEGER NOT NULL DEFAULT 0,
    date_added    TEXT DEFAULT (datetime('now')),
    date_started  TEXT,
    date_finished TEXT
);
CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);
"""

# Columns added after v0.1.0; applied to pre-existing databases on connect.
_MIGRATIONS = (
    ("description", "TEXT DEFAULT ''"),
    ("tags", "TEXT DEFAULT ''"),
    ("current_page", "INTEGER NOT NULL DEFAULT 0"),
)


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(_SCHEMA)
    _migrate(con)
    con.commit()
    return con


def _migrate(con):
    have = {r["name"] for r in con.execute("PRAGMA table_info(books)").fetchall()}
    for name, decl in _MIGRATIONS:
        if name not in have:
            con.execute(f"ALTER TABLE books ADD COLUMN {name} {decl}")


# ---------- reads ----------

def all_books(con):
    return con.execute("SELECT * FROM books ORDER BY date_added DESC").fetchall()


def books_by_status(con, status):
    return con.execute(
        "SELECT * FROM books WHERE status=? ORDER BY date_added DESC", (status,)
    ).fetchall()


def get_book(con, book_id):
    return con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()


def counts_by_status(con):
    rows = con.execute("SELECT status, COUNT(*) AS n FROM books GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


# ---------- writes ----------

def add_book(con, *, title, author="", year=0, pages=0, olid="", isbn="",
             cover_url="", cover_path="", status="want"):
    """Insert a book. If a book with the same Open Library id already exists,
    return the existing id instead of duplicating it."""
    if olid:
        existing = con.execute("SELECT id FROM books WHERE olid=?", (olid,)).fetchone()
        if existing:
            return existing["id"]
    cur = con.execute(
        """INSERT INTO books (olid, isbn, title, author, year, pages, cover_url,
                              cover_path, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (olid, isbn, title, author, year, pages, cover_url, cover_path, status),
    )
    con.commit()
    return cur.lastrowid


def import_book(con, *, title, author="", year=0, pages=0, olid="", isbn="",
                status="want", rating=0, notes="", tags="", current_page=0,
                description="", date_started=None, date_finished=None,
                date_added=None):
    """Insert a book from an external import, carrying its rating, notes, tags
    and historical dates. Deduplicates against an existing book by Open Library
    id (or, lacking one, by case-insensitive title + author) so re-importing the
    same file doesn't create duplicates. Returns (book_id, created)."""
    existing = None
    if olid:
        existing = con.execute("SELECT id FROM books WHERE olid=?", (olid,)).fetchone()
    if existing is None:
        existing = con.execute(
            "SELECT id FROM books WHERE lower(title)=lower(?) "
            "AND lower(author)=lower(?)", (title, author)).fetchone()
    if existing:
        return existing["id"], False

    status = status if status in STATUSES else "want"
    rating = max(0, min(5, rating))
    cur = con.execute(
        """INSERT INTO books (olid, isbn, title, author, year, pages, status,
                              rating, notes, tags, current_page, description,
                              date_added, date_started, date_finished)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   COALESCE(?, datetime('now')), ?, ?)""",
        (olid, isbn, title, author, year, pages, status, rating, notes, tags,
         max(0, current_page), description, date_added, date_started,
         date_finished),
    )
    con.commit()
    return cur.lastrowid, True


def set_status(con, book_id, status):
    """Move a book to a shelf. Reading-date stamping is driven by the window
    layer (which confirms before overwriting an existing date), so this only
    changes the status."""
    if status not in STATUSES:
        return
    con.execute("UPDATE books SET status=? WHERE id=?", (status, book_id))
    con.commit()


def set_rating(con, book_id, rating):
    con.execute("UPDATE books SET rating=? WHERE id=?", (max(0, min(5, rating)), book_id))
    con.commit()


def set_notes(con, book_id, notes):
    con.execute("UPDATE books SET notes=? WHERE id=?", (notes, book_id))
    con.commit()


def set_tags(con, book_id, tags):
    con.execute("UPDATE books SET tags=? WHERE id=?", ((tags or "").strip(), book_id))
    con.commit()


def set_progress(con, book_id, current_page):
    con.execute("UPDATE books SET current_page=? WHERE id=?",
                (max(0, int(current_page)), book_id))
    con.commit()


def all_tags(con):
    """Return the distinct tags used across the library, sorted, for suggestions."""
    seen = {}
    for row in con.execute("SELECT tags FROM books WHERE tags != ''"):
        for tag in (row["tags"] or "").split(","):
            tag = tag.strip()
            if tag:
                seen[tag.lower()] = tag
    return [seen[k] for k in sorted(seen)]


def set_description(con, book_id, description):
    con.execute("UPDATE books SET description=? WHERE id=?", (description, book_id))
    con.commit()


def set_book_date(con, book_id, field, value):
    """Set a reading date. `field` is whitelisted to avoid SQL injection;
    `value` is an ISO date string ("YYYY-MM-DD") or None to clear it."""
    if field not in ("date_started", "date_finished"):
        return
    con.execute(f"UPDATE books SET {field}=? WHERE id=?", (value, book_id))
    con.commit()


def set_cover(con, book_id, cover_path):
    con.execute("UPDATE books SET cover_path=? WHERE id=?", (cover_path, book_id))
    con.commit()


# Columns that "Link to fetch details" may fill in from an external reference.
# Whitelisted so the caller can't touch status/rating/dates through this path.
_UPDATABLE = ("author", "year", "pages", "isbn", "olid", "description",
              "cover_url")


def update_fields(con, book_id, **fields):
    """Update a whitelisted set of metadata columns in one statement. Unknown
    keys are ignored; a no-op when nothing whitelisted is supplied."""
    cols = [(k, v) for k, v in fields.items() if k in _UPDATABLE]
    if not cols:
        return
    assignments = ", ".join(f"{name}=?" for name, _ in cols)
    values = [value for _, value in cols] + [book_id]
    con.execute(f"UPDATE books SET {assignments} WHERE id=?", values)
    con.commit()


def books_without_cover(con):
    """Books whose cover_path is empty — targets for 'Look for missing covers'."""
    return con.execute(
        "SELECT * FROM books WHERE cover_path IS NULL OR cover_path=''"
    ).fetchall()


def delete_book(con, book_id):
    con.execute("DELETE FROM books WHERE id=?", (book_id,))
    con.commit()


def wipe_library(con):
    con.execute("DELETE FROM books")
    con.commit()
