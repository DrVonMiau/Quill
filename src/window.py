"""Quill's main window: a master-detail reading tracker.

Shares Lyre/Easel's chrome — a custom titlebar with the window controls, a
cover-size slider and the menu; a navigation band of shelf tabs on the grey
desktop; and a rounded "paper" card that holds the library grid. Selecting a
cover reveals a floating info panel on the right (cover, a three-way status
control, a star rating, metadata and an autosaving summary).
"""
import datetime
import threading
import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import csvimport
from . import library as lib
from . import openlibrary as ol
from .models import Book
from .widgets import Cover

APP_ID = "io.github.drvonmiau.Quill"

# Shelves shown as tabs, in the order the design lays them out. Each maps to a
# stored book status.
SHELVES = ("read", "reading", "want")
SHELF_LABELS = {
    "read": "Read",
    "reading": "Reading",
    "want": "To read",
}
STATUS_ICONS = {
    "read": "quill-status-read-symbolic",
    "reading": "quill-status-reading-symbolic",
    "want": "quill-status-toread-symbolic",
}

SORT_OPTIONS = [
    ("Recently added", "recent"),
    ("Title", "title"),
    ("Author", "author"),
    ("Rating", "rating"),
]

THEME_SCHEMES = {
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
    "system": Adw.ColorScheme.DEFAULT,
}

POINTER_CURSOR = Gdk.Cursor.new_from_name("pointer")

SPACE_L = 24
INFO_WIDTH = 300          # the floating info panel
DETAIL_COVER_W = 280      # cover width inside the 300px info panel


@Gtk.Template(resource_path="/io/github/drvonmiau/Quill/window.ui")
class QuillWindow(Adw.ApplicationWindow):
    __gtype_name__ = "QuillWindow"

    toast_overlay = Gtk.Template.Child()

    titlebar_box = Gtk.Template.Child()
    titlebar_spacer = Gtk.Template.Child()
    wc_start = Gtk.Template.Child()
    cover_scale = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()

    nav_row = Gtk.Template.Child()
    middle_stack = Gtk.Template.Child()
    tab_read = Gtk.Template.Child()
    tab_reading = Gtk.Template.Child()
    tab_toread = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    add_btn = Gtk.Template.Child()
    sort_btn = Gtk.Template.Child()
    search_toggle_btn = Gtk.Template.Child()

    content_row = Gtk.Template.Child()
    paper_stack = Gtk.Template.Child()
    book_grid = Gtk.Template.Child()

    info_revealer = Gtk.Template.Child()
    info_panel = Gtk.Template.Child()
    detail_cover_slot = Gtk.Template.Child()
    status_read_btn = Gtk.Template.Child()
    status_reading_btn = Gtk.Template.Child()
    status_toread_btn = Gtk.Template.Child()
    detail_title = Gtk.Template.Child()
    detail_author = Gtk.Template.Child()
    detail_date = Gtk.Template.Child()
    detail_pages = Gtk.Template.Child()
    detail_rating_box = Gtk.Template.Child()
    detail_notes = Gtk.Template.Child()
    detail_remove_btn = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.con = lib.connect()
        self.settings = Gio.Settings.new(APP_ID)

        self.shelf = "reading"
        self._books_all = []
        self._search_query = ""
        self._detail_book_id = None
        self._detail_cover = None
        self._loading_detail = False
        self._notes_timer = 0
        self._cover_size_timer = 0
        self._grid_refresh_timer = 0
        self._surface_width = 0
        self._surface_height = 0
        self._sort = self.settings.get_string("sort-books")
        self._cover_w = self.settings.get_int("cover-size")

        self._tab_buttons = {
            "read": self.tab_read,
            "reading": self.tab_reading,
            "want": self.tab_toread,
        }
        self._status_buttons = {
            "read": self.status_read_btn,
            "reading": self.status_reading_btn,
            "want": self.status_toread_btn,
        }

        self._setup_actions()
        self._setup_grid()
        self._setup_stars()
        self._setup_status_control()
        self._setup_notes()
        self._setup_cover_slider()

        for shelf, btn in self._tab_buttons.items():
            btn.connect("clicked", lambda _b, s=shelf: self._select_tab(s))
        self.add_btn.connect("clicked", lambda *_: self._open_add_dialog())
        self.search_toggle_btn.connect("toggled", self._on_toggle_search)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search",
                                  lambda *_: self.search_toggle_btn.set_active(False))
        self.detail_remove_btn.connect("clicked", lambda *_: self._confirm_delete())

        self.connect("close-request", self._on_close_request)
        self.connect("realize", self._on_realize)

        self._setup_theme()
        self._setup_titlebar_sides()
        self._restore_state()
        self._build_sort_menu()
        self._apply_pointer_cursors()
        self._load_books()

    # ---------- actions ----------

    def _setup_actions(self):
        add = Gio.SimpleAction.new("add-book", None)
        add.connect("activate", lambda *_a: self._open_add_dialog())
        self.add_action(add)

        prefs = Gio.SimpleAction.new("preferences", None)
        prefs.connect("activate", lambda *_a: self._open_preferences())
        self.add_action(prefs)

        imp = Gio.SimpleAction.new("import-csv", None)
        imp.connect("activate", lambda *_a: self._open_import_dialog())
        self.add_action(imp)

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

    # ---------- titlebar / layout ----------

    @staticmethod
    def _close_button_is_left(layout):
        left = (layout or "").split(":")[0]
        return "close" in left

    def _setup_titlebar_sides(self):
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.connect("notify::gtk-decoration-layout",
                             lambda *_a: self._apply_titlebar_side())
        self._apply_titlebar_side()

    def _apply_titlebar_side(self):
        """Keep the cover-size slider + menu on the OPPOSITE side of the window
        controls, whichever side the system puts them."""
        settings = Gtk.Settings.get_default()
        layout = settings.get_property("gtk-decoration-layout") if settings else ""
        aux = (self.cover_scale, self.menu_button)
        box = self.titlebar_box
        if self._close_button_is_left(layout):
            box.reorder_child_after(self.titlebar_spacer, self.wc_start)
            previous = self.titlebar_spacer
        else:
            previous = self.wc_start
        for widget in aux:
            box.reorder_child_after(widget, previous)
            previous = widget
        if not self._close_button_is_left(layout):
            box.reorder_child_after(self.titlebar_spacer, previous)

    def _apply_pointer_cursors(self):
        def walk(widget):
            if isinstance(widget, Gtk.WindowControls):
                return
            if isinstance(widget, (Gtk.Button, Gtk.Scale)):
                widget.set_cursor(POINTER_CURSOR)
            child = widget.get_first_child()
            while child:
                walk(child)
                child = child.get_next_sibling()
        walk(self)

    def _on_realize(self, *_args):
        surface = self.get_surface()
        if surface is not None:
            surface.connect("notify::width", self._on_surface_resize)
            surface.connect("notify::height", self._on_surface_resize)
            self._on_surface_resize(surface, None)

    def _on_surface_resize(self, surface, _pspec):
        self._surface_width = surface.get_width()
        self._surface_height = surface.get_height()
        self._apply_layout_metrics()
        return False

    def _apply_layout_metrics(self):
        """5% top/left/right margins with the paper + info block centered; the
        info panel is a fixed 300px floating panel to the paper's right."""
        width, height = self._surface_width, self._surface_height
        if width <= 0 or height <= 0:
            return
        margin_y = round(height * 0.05)
        margin_x = max(SPACE_L, round(width * 0.05))
        revealed = self.info_revealer.get_reveal_child()
        if revealed:
            gap = round(width * 0.05)
            ideal_paper = round(width * 0.60)
            centered = (width - ideal_paper - gap - INFO_WIDTH) // 2
            margin_x = max(margin_x, centered)
        else:
            gap = 0
        self.content_row.set_margin_start(margin_x)
        self.content_row.set_margin_end(margin_x)
        self.content_row.set_margin_top(0)
        self.content_row.set_margin_bottom(margin_y)
        self.nav_row.set_margin_start(margin_x)
        self.nav_row.set_margin_end(margin_x + (gap + INFO_WIDTH if revealed else 0))
        # Pin the panel to a fixed width so it never competes with the paper
        # for slack (the paper alone absorbs it).
        self.info_panel.set_size_request(INFO_WIDTH if revealed else 0, -1)
        self.info_revealer.set_margin_start(gap)

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
        self._select_tab(saved if saved in SHELVES else "reading")

    def _on_close_request(self, *_args):
        self._flush_notes()
        self.settings.set_boolean("window-maximized", self.is_maximized())
        if not self.is_maximized():
            width, height = self.get_default_size()
            self.settings.set_int("window-width", width)
            self.settings.set_int("window-height", height)
        self.settings.set_string("last-shelf", self.shelf)
        self.settings.set_int("cover-size", self._cover_w)
        return False

    def _toast(self, text):
        self.toast_overlay.add_toast(Adw.Toast.new(text))

    # ---------- cover-size slider ----------

    def _setup_cover_slider(self):
        adj = self.cover_scale.get_adjustment()
        self._cover_w = max(int(adj.get_lower()), min(int(adj.get_upper()), self._cover_w))
        adj.set_value(self._cover_w)
        self.cover_scale.connect("value-changed", self._on_cover_size_changed)

    def _on_cover_size_changed(self, scale):
        self._cover_w = int(scale.get_value())
        if self._cover_size_timer:
            GLib.source_remove(self._cover_size_timer)
        self._cover_size_timer = GLib.timeout_add(90, self._apply_cover_size)

    def _apply_cover_size(self):
        self._cover_size_timer = 0
        self.settings.set_int("cover-size", self._cover_w)
        self._apply_filter()  # re-splice so every tile rebinds at the new size
        return False

    # ---------- tabs ----------

    def _select_tab(self, shelf):
        self.shelf = shelf
        for key, btn in self._tab_buttons.items():
            if key == shelf:
                btn.add_css_class("tab-active")
            else:
                btn.remove_css_class("tab-active")
        self.settings.set_string("last-shelf", shelf)
        self._close_detail()
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

    # ---------- grid ----------

    def _setup_grid(self):
        self.book_store = Gio.ListStore(item_type=Book)
        self.selection = Gtk.SingleSelection(model=self.book_store)
        self.selection.set_autoselect(False)
        self.selection.set_can_unselect(True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.book_grid.set_model(self.selection)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(self._card_widget()))
        factory.connect("bind", lambda _f, item: self._bind_card(item))
        self.book_grid.set_factory(factory)
        self.book_grid.set_single_click_activate(True)
        self.book_grid.connect("activate", self._on_grid_activate)

    def _on_grid_activate(self, grid, pos):
        item = grid.get_model().get_item(pos)
        if item is not None:
            self._open_book(item.id)

    def _card_widget(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.add_css_class("card-box")
        box.set_cursor(POINTER_CURSOR)
        cover = Cover("", width=self._cover_w)
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
        box.cover.set_size(self._cover_w)
        box.cover.set_placeholder(book.title[:18] if not book.cover_path else "")
        box.cover.set_path(book.cover_path or None)
        box.title.set_label(book.title)
        box.author.set_label(book.author or "Unknown author")

    # ---------- loading / filtering ----------

    def _load_books(self):
        self._books_all = self._read_all()
        self._apply_filter()

    def _read_all(self):
        return [
            Book(id=r["id"], title=r["title"], author=r["author"], year=r["year"],
                 pages=r["pages"], cover_path=r["cover_path"], status=r["status"],
                 rating=r["rating"], olid=r["olid"], notes=r["notes"])
            for r in lib.all_books(self.con)
        ]

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
            if b.status != self.shelf:
                continue
            if q and q not in b.title.lower() and q not in b.author.lower():
                continue
            visible.append(b)

        self.book_store.splice(0, self.book_store.get_n_items(), visible)
        self.paper_stack.set_visible_child_name("empty" if not visible else "library")
        self._restore_grid_selection(visible)

    def _restore_grid_selection(self, visible):
        if self._detail_book_id is None:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
            return
        for i, b in enumerate(visible):
            if b.id == self._detail_book_id:
                self.selection.set_selected(i)
                return
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)

    def _reload_grid(self):
        """Refresh grid data while keeping the detail panel open."""
        self._books_all = self._read_all()
        self._apply_filter()

    # ---------- detail panel ----------

    def _setup_status_control(self):
        for status, btn in self._status_buttons.items():
            btn.connect("clicked", self._on_status_clicked, status)

    def _setup_stars(self):
        self._star_btns = []
        for i in range(5):
            btn = Gtk.Button(icon_name="quill-star-empty-symbolic",
                             css_classes=["flat", "star-btn", "star-empty"],
                             valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda _b, n=i + 1: self._on_star_clicked(n))
            self._star_btns.append(btn)
            self.detail_rating_box.append(btn)

    def _render_stars(self, rating):
        for i, btn in enumerate(self._star_btns):
            filled = i < rating
            btn.set_icon_name("quill-star-filled-symbolic" if filled
                              else "quill-star-empty-symbolic")
            btn.remove_css_class("star-empty" if filled else "star-filled")
            btn.add_css_class("star-filled" if filled else "star-empty")

    def _render_status_control(self, current):
        for status, btn in self._status_buttons.items():
            active = status == current
            box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            box.append(Gtk.Image(icon_name=STATUS_ICONS[status], pixel_size=20))
            if active:
                box.append(Gtk.Label(label=SHELF_LABELS[status]))
                btn.add_css_class("active")
                btn.set_hexpand(True)
            else:
                btn.remove_css_class("active")
                btn.set_hexpand(False)
            btn.set_child(box)

    def _setup_notes(self):
        self.detail_notes.get_buffer().connect("changed", self._on_notes_changed)

    def _open_book(self, book_id):
        row = lib.get_book(self.con, book_id)
        if not row:
            return
        if self._detail_book_id is not None and self._detail_book_id != book_id:
            self._flush_notes()
        self._loading_detail = True
        self._detail_book_id = book_id

        child = self.detail_cover_slot.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.detail_cover_slot.remove(child)
            child = nxt
        self._detail_cover = Cover(row["title"][:18] if not row["cover_path"] else "",
                                   width=DETAIL_COVER_W)
        self._detail_cover.add_css_class("detail-cover")
        self._detail_cover.set_path(row["cover_path"] or None)
        self.detail_cover_slot.append(self._detail_cover)

        self.detail_title.set_label(row["title"])
        self.detail_author.set_label(row["author"] or "Unknown author")
        self.detail_date.set_label(self._format_dates(row))
        self.detail_pages.set_label(str(row["pages"]) if row["pages"] else "—")

        self._render_status_control(row["status"])
        self._render_stars(row["rating"])
        self.detail_notes.get_buffer().set_text(row["notes"] or "")

        self.info_revealer.set_reveal_child(True)
        self._apply_layout_metrics()
        self._loading_detail = False

    def _close_detail(self):
        self._flush_notes()
        self._detail_book_id = None
        self.info_revealer.set_reveal_child(False)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self._apply_layout_metrics()

    @staticmethod
    def _format_dates(row):
        started = QuillWindow._fmt_date(row["date_started"])
        finished = QuillWindow._fmt_date(row["date_finished"])
        if started and finished:
            return f"{started}  ·  {finished}"
        if finished:
            return f"Finished {finished}"
        if started:
            return f"Started {started}"
        return "—"

    @staticmethod
    def _fmt_date(value):
        if not value:
            return None
        try:
            d = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            return None
        return f"{d.day} {d.strftime('%b')} {d.year}"

    def _on_status_clicked(self, _btn, status):
        if self._detail_book_id is None:
            return
        lib.set_status(self.con, self._detail_book_id, status)
        row = lib.get_book(self.con, self._detail_book_id)
        if row:
            self._render_status_control(row["status"])
            self.detail_date.set_label(self._format_dates(row))
        self._reload_grid()
        self._toast(f"Moved to {SHELF_LABELS[status]}")

    def _on_star_clicked(self, n):
        if self._detail_book_id is None:
            return
        row = lib.get_book(self.con, self._detail_book_id)
        rating = 0 if row and row["rating"] == n else n
        lib.set_rating(self.con, self._detail_book_id, rating)
        self._render_stars(rating)
        self._reload_grid()

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
        self._close_detail()
        self._load_books()
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

        shelf_dd = Gtk.DropDown.new_from_strings([SHELF_LABELS[s] for s in SHELVES])
        shelf_dd.set_selected(SHELVES.index("want"))
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
                except Exception as exc:
                    results, err = [], str(exc)
                GLib.idle_add(deliver, token, results, err)

            threading.Thread(target=work, daemon=True).start()
            return False

        def deliver(token, results, err):
            if token != state["token"]:
                return False
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
        idx = shelf_dd.get_selected()
        status = SHELVES[idx] if 0 <= idx < len(SHELVES) else "want"
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
        self._schedule_grid_refresh()
        if self._detail_book_id == book_id and self._detail_cover is not None:
            self._detail_cover.set_placeholder("")
            self._detail_cover.set_path(path)
        return False

    def _schedule_grid_refresh(self):
        """Coalesce grid rebuilds when many covers land at once (bulk import)."""
        if self._grid_refresh_timer:
            return
        self._grid_refresh_timer = GLib.timeout_add(400, self._do_grid_refresh)

    def _do_grid_refresh(self):
        self._grid_refresh_timer = 0
        self._reload_grid()
        return False

    # ---------- import from CSV ----------

    def _open_import_dialog(self):
        dialog = Gtk.FileDialog(title="Import Books from CSV")
        csv_filter = Gtk.FileFilter()
        csv_filter.set_name("CSV files")
        csv_filter.add_suffix("csv")
        csv_filter.add_mime_type("text/csv")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(csv_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(csv_filter)
        dialog.open(self, None, self._on_import_file_chosen)

    def _on_import_file_chosen(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # dismissed
        path = gfile.get_path() if gfile is not None else None
        if path:
            self._run_import(path)

    def _run_import(self, path):
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                books = csvimport.parse_openreads(fh)
        except Exception:
            self._toast("Couldn't read that CSV file.")
            return

        created, to_fetch = 0, []
        for b in books:
            book_id, was_created = lib.import_book(self.con, **b)
            if was_created:
                created += 1
                if b["olid"] or b["isbn"]:
                    to_fetch.append((book_id, b["olid"], b["isbn"]))

        self._load_books()
        skipped = len(books) - created
        msg = f"Imported {created} book{'' if created == 1 else 's'}"
        if skipped:
            msg += f" · {skipped} already in library"
        self._toast(msg)
        if to_fetch:
            self._fetch_covers_bulk(to_fetch)

    def _fetch_covers_bulk(self, items):
        """Fetch covers for imported books by OLID/ISBN on one background
        thread, paced so Open Library isn't hammered."""
        def work():
            for book_id, olid, isbn in items:
                dest = lib.COVERS_DIR / f"{book_id}.jpg"
                try:
                    path = ol.download_cover_by_key(dest, olid=olid, isbn=isbn)
                except Exception:
                    path = None
                if path:
                    GLib.idle_add(self._cover_ready, book_id, path)
                time.sleep(0.2)

        threading.Thread(target=work, daemon=True).start()

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
            self._close_detail()
            self._load_books()
            self._toast("Library cleared")
            prefs_dialog.close()

        confirm.connect("response", on_response)
        confirm.present(self)
