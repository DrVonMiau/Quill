"""GObject model wrapping a book row, so a Gio.ListStore can drive the grid."""
from gi.repository import GObject


class Book(GObject.Object):
    __gtype_name__ = "QuillBook"

    def __init__(self, id, title, author="", year=0, pages=0, cover_path="",
                 status="want", rating=0, olid="", notes="", isbn="",
                 description="", tags="", current_page=0,
                 date_started=None, date_finished=None):
        super().__init__()
        self.id = id
        self.title = title
        self.author = author or ""
        self.year = year or 0
        self.pages = pages or 0
        self.cover_path = cover_path or ""
        self.status = status or "want"
        self.rating = rating or 0
        self.olid = olid or ""
        self.isbn = isbn or ""
        self.notes = notes or ""
        self.description = description or ""
        self.tags = tags or ""
        self.current_page = current_page or 0
        self.date_started = date_started
        self.date_finished = date_finished
