"""Quill's main window: shelves of books, a cover grid, a book detail with
status/rating/notes, and an "Add a book" dialog backed by Open Library.

The visual language (grey desktop + paper card + segmented pill tabs) is
carried over from the sibling app Lyre; the domain is books, not music.
"""
import threading

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import library as lib
from . import openlibrary as ol
from .models import Book
from .widgets import Cover

APP_ID = "io.github.drvonmiau.Quill"

# Shelves shown as tabs. "all" is every book; the rest map to a status.
SHELVES = ("all", "reading", "read", "want")
SHELF_LABELS = {
    "all": "All",
    "reading": "Reading",
    "read": "Read",
    "want": "Want to read",
}

# Sort options for the library grid.
SORT_OPTIONS = [
    ("Recently added", "recent"),
    ("Title", "title"),
    ("Author", "author"),
    ("Rating", "rating"),
]

# Cover dimensions used everywhere, so every card is identical by construction.
COVER_W = 140
CARD_W = 156

THEME_SCHEMES = {
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
    "system": Adw.ColorScheme.DEFAULT,
}

POINTER_CURSOR = Gdk.Cursor.new_from_name("pointer")


@Gtk.Template(resource_path="/io/github/drvonmiau/Quill/window.ui")
class QuillWindow(Adw.ApplicationWindow):
    __gtype_name__ = "QuillWindow"

    toast_overlay = Gtk.Template.Child()
    add_btn = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    search_toggle_btn = Gtk.Template.Child()
    sort_btn = Gtk.Template.Child()

    nav_row = Gtk.Template.Child()
    middle_stack = Gtk.Template.Child()
    tab_all = Gtk.Template.Child()
    tab_reading = Gtk.Template.Child()
    tab_read = Gtk.Template.Child()
    tab_want = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()

    paper_stack = Gtk.Template.Child()
    book_grid = Gtk.Template.Child()
    add_first_btn = Gtk.Template.Child()

    detail_back_row = Gtk.Template.Child()
    back_btn = Gtk.Template.Child()
    detail_kind_label = Gtk.Template.Child()
    detail_cover_slot = Gtk.Template.Child()
    detail_title = Gtk.Template.Child()
    detail_author = Gtk.Template.Child()
    detail_meta = Gtk.Template.Child()
    detail_status_dd = Gtk.Template.Child()
    detail_rating_box = Gtk.Template.Child()
    detail_notes = Gtk.Template.Child()
    detail_remove_btn = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.con = lib.connect()
        self.settings = Gio.Settings.new(APP_ID)

        self.shelf = "all"
        self._books_all = []
        self._search_query = ""
        self._detail_book_id = None
        self._detail_cover = None
        self._loading_detail = False
        self._notes_timer = 0
        self._sort = self.settings.get_string("sort-books")

        self._tab_buttons = {
            "all": self.tab_all,
            "reading": self.tab_reading,
            "read": self.tab_read,
            "want": self.tab_want,
        }

        self._setup_actions()
        self._setup_grid()
        self._setup_stars()
        self._setup_status_dropdown()
        self._setup_notes()

        for shelf, btn in self._tab_buttons.items():
            btn.connect("clicked", lambda _b, s=shelf: self._select_shelf(s))
        self.add_btn.connect("clicked", lambda *_: self._open_add_dialog())
        self.add_first_btn.connect("clicked", lambda *_: self._open_add_dialog())
        self.back_btn.connect("clicked", lambda *_: self._go_back())
        self.search_toggle_btn.connect("toggled", self._on_toggle_search)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search",
                                  lambda *_: self.search_toggle_btn.set_active(False))
        self.detail_remove_btn.connect("clicked", lambda *_: self._confirm_delete())

        self.connect("close-request", self._on_close_request)

        self._setup_theme()
        self._restore_state()
        self._build_sort_menu()
        self._load_books()

    # ---------- actions / chrome ----------

    def _setup_actions(self):
        add = Gio.SimpleAction.new("add-book", None)
        add.connect("activate", lambda *_a: self._open_add_dialog())
        self.add_action(add)

        prefs = Gio.SimpleAction.new("preferences", None)
        prefs.connect("activate", lambda *_a: self._open_preferences())
        self.add_action(prefs)

        find = Gio.SimpleAction.new("find", None)
        find.connect("activate", lambda *_a: self.search_toggle_btn.set_active(
            not self.search_toggle_btn.get_active()))
        self.add_action(find)

        sort_mode = Gio.SimpleAction.new_stateful(
            "sort-mode", GLib.VariantType.new("s"), GLib.Variant("s", self._sort))
        sort_mode.connect("activate", self._on_sort_mode)
        self.add_action(sort_mode)

        app = self.get_application()
        if app is not None:
            app.set_accels_for_action("win.add-book", ["<primary>n"])
            app.set_accels_for_action("win.find", ["<primary>f"])

    def _build_sort_menu(self):
        menu = Gio.Menu()
        section = Gio.Menu()
        for label, mode in SORT_OPTIONS:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.sort-mode", GLib.Variant("s", mode))
            section.append_item(item)
        menu.append_section("Sort by", section)
        self.sort_btn.set_menu_model(menu)

    def _on_sort_mode(self, action, param):
        mode = param.get_string()
        action.set_state(param)
        self._sort = mode
        self.settings.set_string("sort-books", mode)
        self._apply_filter()

    def _on_toggle_search(self, btn):
        active = btn.get_active()
        self.middle_stack.set_visible_child_name("search" if active else "view")
        if active:
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")

    def _on_search_changed(self, entry):
        self._search_query = entry.get_text().strip().lower()
        self._apply_filter()

    # ---------- theme + window state ----------

    def _setup_theme(self):
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", self._on_dark_changed)
        self._apply_theme(self.settings.get_string("theme"))

    def _apply_theme(self, theme):
        Adw.StyleManager.get_default().set_color_scheme(
            THEME_SCHEMES.get(theme, Adw.ColorScheme.DEFAULT))
        self._on_dark_changed()

    def _on_dark_changed(self, *_args):
        if Adw.StyleManager.get_default().get_dark():
            self.add_css_class("dark")
        else:
            self.remove_css_class("dark")

    def _restore_state(self):
        self.set_default_size(self.settings.get_int("window-width"),
                              self.settings.get_int("window-height"))
        if self.settings.get_boolean("window-maximized"):
            self.maximize()
        saved = self.settings.get_string("last-shelf")
        self.shelf = saved if saved in SHELVES else "all"
        self._highlight_shelf()

    def _on_close_request(self, *_args):
        self._flush_notes()
        self.settings.set_boolean("window-maximized", self.is_maximized())
        if not self.is_maximized():
            width, height = self.get_default_size()
            self.settings.set_int("window-width", width)
            self.settings.set_int("window-height", height)
        self.settings.set_string("last-shelf", self.shelf)
        return False

    def _toast(self, text):
        self.toast_overlay.add_toast(Adw.Toast.new(text))

    # ---------- grid ----------

    def _setup_grid(self):
        self.book_store = Gio.ListStore(item_type=Book)
        self.book_grid.set_model(Gtk.SingleSelection(model=self.book_store))
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(Gtk.Box()))
        factory.connect("bind", lambda _f, item: self._bind_card(item))
        self.book_grid.set_factory(factory)
        self.book_grid.set_single_click_activate(True)
        self.book_grid.connect(
            "activate", lambda g, pos: self._open_book(g.get_model().get_item(pos).id))

    def _card_widget(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=CARD_W,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        box.add_css_class("card-box")
        box.set_cursor(POINTER_CURSOR)
        cover = Cover("", width=COVER_W)
        cover.add_css_class("card-cover")
        cover.set_halign(Gtk.Align.CENTER)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, wrap=True, lines=2,
                          max_width_chars=16, css_classes=["card-title"])
        title.set_valign(Gtk.Align.START)
        author = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                           max_width_chars=18, css_classes=["card-subtitle"])
        box.append(cover)
        box.append(title)
        box.append(author)
        box.cover, box.title, box.author = cover, title, author
        return box

    def _bind_card(self, item):
        book = item.get_item()
        box = item.get_child()
        if not hasattr(box, "cover"):
            box = self._card_widget()
            item.set_child(box)
        box.cover.set_placeholder(book.title[:18] if not book.cover_path else "")
        box.cover.set_path(book.cover_path or None)
        box.title.set_label(book.title)
        box.author.set_label(book.author or "Unknown author")

    # ---------- loading / filtering ----------

    def _load_books(self):
        self._books_all = [
            Book(id=r["id"], title=r["title"], author=r["author"], year=r["year"],
                 pages=r["pages"], cover_path=r["cover_path"], status=r["status"],
                 rating=r["rating"], olid=r["olid"], notes=r["notes"])
            for r in lib.all_books(self.con)
        ]
        self._apply_filter()

    def _sorted(self, books):
        mode = self._sort
        if mode == "title":
            return sorted(books, key=lambda b: b.title.lower())
        if mode == "author":
            return sorted(books, key=lambda b: (b.author.lower(), b.title.lower()))
        if mode == "rating":
            return sorted(books, key=lambda b: (-b.rating, b.title.lower()))
        return books  # "recent" — library order is already newest-first

    def _apply_filter(self):
        q = self._search_query
        visible = []
        for b in self._sorted(self._books_all):
            if self.shelf != "all" and b.status != self.shelf:
                continue
            if q and q not in b.title.lower() and q not in b.author.lower():
                continue
            visible.append(b)

        if not self._books_all:
            self.paper_stack.set_visible_child_name("empty")
        elif self.paper_stack.get_visible_child_name() not in ("detail",):
            self.paper_stack.set_visible_child_name("library")

        self.book_store.splice(0, self.book_store.get_n_items(), visible)

    def _select_shelf(self, shelf):
        self.shelf = shelf
        self._highlight_shelf()
        self.detail_back_row.set_visible(False)
        if self.paper_stack.get_visible_child_name() == "detail":
            self.paper_stack.set_visible_child_name("library")
        self._apply_filter()

    def _highlight_shelf(self):
        for key, btn in self._tab_buttons.items():
            if key == self.shelf:
                btn.add_css_class("tab-active")
            else:
                btn.remove_css_class("tab-active")

    # ---------- detail ----------

    def _setup_status_dropdown(self):
        self.detail_status_dd.set_model(
            Gtk.StringList.new([lib.STATUS_LABELS[s] for s in lib.STATUSES]))
        self.detail_status_dd.connect("notify::selected", self._on_status_changed)

    def _setup_stars(self):
        self._star_btns = []
        for i in range(5):
            btn = Gtk.Button(icon_name="non-starred-symbolic",
                             css_classes=["flat", "star-btn"], valign=Gtk.Align.CENTER)
            btn.set_cursor(POINTER_CURSOR)
            btn.connect("clicked", lambda _b, n=i + 1: self._on_star_clicked(n))
            self._star_btns.append(btn)
            self.detail_rating_box.append(btn)

    def _render_stars(self, rating):
        for i, btn in enumerate(self._star_btns):
            filled = i < rating
            btn.set_icon_name("starred-symbolic" if filled else "non-starred-symbolic")
            if filled:
                btn.add_css_class("filled")
            else:
                btn.remove_css_class("filled")

    def _setup_notes(self):
        self.detail_notes.get_buffer().connect("changed", self._on_notes_changed)

    def _open_book(self, book_id):
        row = lib.get_book(self.con, book_id)
        if not row:
            return
        self._loading_detail = True
        self._detail_book_id = book_id
        self.detail_kind_label.set_label(lib.STATUS_LABELS.get(row["status"], "Book"))

        child = self.detail_cover_slot.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.detail_cover_slot.remove(child)
            child = nxt
        self._detail_cover = Cover("cover", width=176)
        self._detail_cover.add_css_class("card-cover")
        self._detail_cover.set_path(row["cover_path"] or None)
        self.detail_cover_slot.append(self._detail_cover)

        self.detail_title.set_label(row["title"])
        self.detail_author.set_label(row["author"] or "Unknown author")
        meta = []
        if row["year"]:
            meta.append(str(row["year"]))
        if row["pages"]:
            meta.append(f"{row['pages']} pages")
        self.detail_meta.set_label("  ·  ".join(meta))
        self.detail_meta.set_visible(bool(meta))

        try:
            self.detail_status_dd.set_selected(lib.STATUSES.index(row["status"]))
        except ValueError:
            self.detail_status_dd.set_selected(0)
        self._render_stars(row["rating"])
        self.detail_notes.get_buffer().set_text(row["notes"] or "")

        self.paper_stack.set_visible_child_name("detail")
        self.detail_back_row.set_visible(True)
        self._loading_detail = False

    def _on_status_changed(self, dropdown, _pspec):
        if self._loading_detail or self._detail_book_id is None:
            return
        idx = dropdown.get_selected()
        if 0 <= idx < len(lib.STATUSES):
            status = lib.STATUSES[idx]
            lib.set_status(self.con, self._detail_book_id, status)
            self.detail_kind_label.set_label(lib.STATUS_LABELS[status])
            self._load_books()

    def _on_star_clicked(self, n):
        if self._detail_book_id is None:
            return
        row = lib.get_book(self.con, self._detail_book_id)
        # Clicking the current rating clears it (toggle off).
        rating = 0 if row and row["rating"] == n else n
        lib.set_rating(self.con, self._detail_book_id, rating)
        self._render_stars(rating)
        self._load_books()

    def _on_notes_changed(self, _buffer):
        if self._loading_detail:
            return
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
        self._notes_timer = GLib.timeout_add(600, self._flush_notes)

    def _flush_notes(self):
        self._notes_timer = 0
        if self._detail_book_id is not None:
            buf = self.detail_notes.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
            lib.set_notes(self.con, self._detail_book_id, text)
        return False

    def _go_back(self):
        self._flush_notes()
        self._detail_book_id = None
        self.detail_back_row.set_visible(False)
        self.paper_stack.set_visible_child_name(
            "empty" if not self._books_all else "library")
        self._apply_filter()

    def _confirm_delete(self):
        if self._detail_book_id is None:
            return
        row = lib.get_book(self.con, self._detail_book_id)
        title = row["title"] if row else "this book"
        dialog = Adw.AlertDialog(
            heading="Remove book?",
            body=f"“{title}” will be removed from your library.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: self._do_delete() if r == "remove" else None)
        dialog.present(self)

    def _do_delete(self):
        book_id = self._detail_book_id
        if book_id is None:
            return
        lib.delete_book(self.con, book_id)
        self._detail_book_id = None
        self._load_books()
        self._go_back()
        self._toast("Book removed")

    # ---------- add via Open Library ----------

    def _open_add_dialog(self):
        dialog = Adw.Dialog()
        dialog.set_title("Add a Book")
        dialog.set_content_width(480)
        dialog.set_content_height(600)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)

        entry = Gtk.SearchEntry(placeholder_text="Search by title or author")
        entry.add_css_class("search-entry")

        shelf_dd = Gtk.DropDown.new_from_strings(
            [lib.STATUS_LABELS[s] for s in lib.STATUSES])
        shelf_row = Gtk.Box(spacing=8)
        shelf_row.append(Gtk.Label(label="Add to", css_classes=["mono-dim"]))
        shelf_row.append(shelf_dd)

        status_lbl = Gtk.Label(label="Type to search Open Library.",
                               css_classes=["mono-dim"], xalign=0)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                              css_classes=["boxed-list"])
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(listbox)

        box.append(entry)
        box.append(shelf_row)
        box.append(status_lbl)
        box.append(scroller)
        dialog.set_child(box)

        state = {"timer": 0, "results": [], "token": 0}

        def run_search():
            state["timer"] = 0
            query = entry.get_text().strip()
            if not query:
                self._clear_listbox(listbox)
                status_lbl.set_label("Type to search Open Library.")
                return False
            status_lbl.set_label("Searching…")
            state["token"] += 1
            token = state["token"]

            def work():
                try:
                    results = ol.search(query)
                    err = None
                except Exception as exc:  # network / parse failure
                    results, err = [], str(exc)
                GLib.idle_add(deliver, token, results, err)

            threading.Thread(target=work, daemon=True).start()
            return False

        def deliver(token, results, err):
            if token != state["token"]:
                return False  # a newer search superseded this one
            state["results"] = results
            self._clear_listbox(listbox)
            if err:
                status_lbl.set_label("Couldn't reach Open Library. Check your connection.")
            elif not results:
                status_lbl.set_label("No matches.")
            else:
                status_lbl.set_label(f"{len(results)} results")
                for i, res in enumerate(results):
                    listbox.append(self._result_row(res, i, shelf_dd, dialog))
            return False

        def on_changed(_e):
            if state["timer"]:
                GLib.source_remove(state["timer"])
            state["timer"] = GLib.timeout_add(400, run_search)

        entry.connect("search-changed", on_changed)
        dialog.present(self)
        entry.grab_focus()

    def _result_row(self, res, index, shelf_dd, dialog):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          css_classes=["card-title"], label=res["title"])
        sub_parts = [res["author"] or "Unknown author"]
        if res["year"]:
            sub_parts.append(str(res["year"]))
        subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                             css_classes=["card-subtitle"], label="  ·  ".join(sub_parts))
        text.append(title)
        text.append(subtitle)
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                         css_classes=["flat", "circular"], tooltip_text="Add")
        add.set_cursor(POINTER_CURSOR)
        add.connect("clicked", lambda *_: self._add_result(res, shelf_dd, dialog))
        box.append(text)
        box.append(add)
        row.set_child(box)
        row.set_activatable(True)
        return row

    def _add_result(self, res, shelf_dd, dialog):
        status = lib.STATUSES[shelf_dd.get_selected()] \
            if 0 <= shelf_dd.get_selected() < len(lib.STATUSES) else "want"
        book_id = lib.add_book(
            self.con, title=res["title"], author=res["author"], year=res["year"],
            pages=res["pages"], olid=res["olid"], isbn=res.get("isbn", ""),
            status=status)
        self._toast(f'Added “{res["title"]}”')
        self._load_books()
        dialog.close()
        cover_i = res.get("cover_i")
        if cover_i:
            self._fetch_cover_async(book_id, cover_i)

    def _fetch_cover_async(self, book_id, cover_i):
        dest = lib.COVERS_DIR / f"{book_id}.jpg"

        def work():
            try:
                path = ol.download_cover(cover_i, dest)
            except Exception:
                path = None
            if path:
                GLib.idle_add(self._cover_ready, book_id, path)

        threading.Thread(target=work, daemon=True).start()

    def _cover_ready(self, book_id, path):
        lib.set_cover(self.con, book_id, path)
        self._load_books()
        if self._detail_book_id == book_id and self._detail_cover is not None:
            self._detail_cover.set_path(path)
        return False

    @staticmethod
    def _clear_listbox(listbox):
        child = listbox.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            listbox.remove(child)
            child = nxt

    # ---------- preferences ----------

    def _open_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()

        appearance = Adw.PreferencesGroup(title="Appearance")
        themes = ("light", "dark", "system")
        theme_row = Adw.ComboRow(title="Theme",
                                 model=Gtk.StringList.new(["Light", "Dark", "System"]))
        current = self.settings.get_string("theme")
        theme_row.set_selected(themes.index(current) if current in themes else 2)

        def on_theme(row, _p):
            theme = themes[row.get_selected()]
            self.settings.set_string("theme", theme)
            self._apply_theme(theme)

        theme_row.connect("notify::selected", on_theme)
        appearance.add(theme_row)
        page.add(appearance)

        danger = Adw.PreferencesGroup(title="Library")
        wipe_row = Adw.ActionRow(title="Delete All Books…", activatable=True)
        wipe_row.add_css_class("error")
        wipe_row.connect("activated", lambda *_: self._confirm_wipe(dialog))
        danger.add(wipe_row)
        page.add(danger)

        dialog.add(page)
        dialog.present(self)

    def _confirm_wipe(self, prefs_dialog):
        confirm = Adw.AlertDialog(
            heading="Delete all books?",
            body="Every book, rating and note will be erased. This cannot be undone.")
        confirm.add_response("cancel", "Cancel")
        confirm.add_response("delete", "Delete")
        confirm.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, r):
            if r != "delete":
                return
            lib.wipe_library(self.con)
            self._detail_book_id = None
            self._go_back()
            self._load_books()
            self._toast("Library cleared")
            prefs_dialog.close()

        confirm.connect("response", on_response)
        confirm.present(self)
