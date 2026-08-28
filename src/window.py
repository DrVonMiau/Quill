"""Quill's main window: a master-detail reading tracker.

Shares Lyre/Easel's chrome — a custom titlebar with the window controls, a
cover-size slider and the menu; a navigation band of shelf tabs on the grey
desktop; and a rounded "paper" card that holds the library grid. Selecting a
cover reveals a floating 350px info panel (cover, a three-way status control, a
star rating, metadata and an Open Library summary).
"""
import datetime
import os
import shutil
import threading
import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import csvexport
from . import csvimport
from . import enrich
from . import googlebooks
from . import library as lib
from . import openlibrary as ol
from .models import Book
from .widgets import Cover, DatePicker

# Search backends, keyed by the setting value and offered in the add dialog.
SEARCH_BACKENDS = [
    ("openlibrary", "Open Library", ol),
    ("googlebooks", "Google Books", googlebooks),
]

APP_ID = "io.github.drvonmiau.Quill"

# Shelves shown as tabs, in the order the design lays them out. Each maps to a
# stored book status.
SHELVES = ("read", "reading", "want", "abandoned")
SHELF_LABELS = {
    "read": "Read",
    "reading": "Reading",
    "want": "To read",
    "abandoned": "Abandoned",
}
# The three-way status control in the detail panel (Abandoned is set via its own
# button, not this control).
STATUS_CONTROL = ("read", "reading", "want")
STATUS_ICONS = {
    "read": "quill-status-read-symbolic",
    "reading": "quill-status-reading-symbolic",
    "want": "quill-status-toread-symbolic",
}

SORT_OPTIONS = [
    ("Finished date", "finished"),
    ("Recently added", "recent"),
    ("Title", "title"),
    ("Author", "author"),
    ("Rating", "rating"),
]

# Four discrete cover-tile widths for the header slider (portrait 1.5 ratio).
COVER_SIZES = [128, 156, 188, 220]

THEME_SCHEMES = {
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
    "system": Adw.ColorScheme.DEFAULT,
}

POINTER_CURSOR = Gdk.Cursor.new_from_name("pointer")

SPACE_L = 24
INFO_WIDTH = 350          # the floating info panel's full width
# The panel's content is inset from the right by this much (info-panel box
# margin-end in the .ui) so the overlay scrollbar floats over padding, never
# over the right-aligned detail values.
PANEL_WIDTH = INFO_WIDTH
DETAIL_COVER_W = 300      # cover width inside the info panel


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
    tab_abandoned = Gtk.Template.Child()
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
    detail_more_btn = Gtk.Template.Child()
    detail_title = Gtk.Template.Child()
    detail_author = Gtk.Template.Child()
    detail_date_btn = Gtk.Template.Child()
    detail_pages = Gtk.Template.Child()
    detail_progress_btn = Gtk.Template.Child()
    detail_progress_bar = Gtk.Template.Child()
    detail_rating_box = Gtk.Template.Child()
    detail_tags_btn = Gtk.Template.Child()
    detail_summary = Gtk.Template.Child()
    detail_readmore_btn = Gtk.Template.Child()

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
            "abandoned": self.tab_abandoned,
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
        self._setup_cover_slider()

        for shelf, btn in self._tab_buttons.items():
            btn.connect("clicked", lambda _b, s=shelf: self._select_tab(s))
        self.add_btn.connect("clicked", lambda *_: self._open_add_dialog())
        self.search_toggle_btn.connect("toggled", self._on_toggle_search)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search",
                                  lambda *_: self.search_toggle_btn.set_active(False))
        self.detail_readmore_btn.connect("clicked", lambda *_: self._toggle_readmore())

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

        manual = Gio.SimpleAction.new("add-manual", None)
        manual.connect("activate", lambda *_a: self._open_manual_dialog())
        self.add_action(manual)

        imp = Gio.SimpleAction.new("import-csv", None)
        imp.connect("activate", lambda *_a: self._open_import_dialog())
        self.add_action(imp)

        exp = Gio.SimpleAction.new("export-csv", None)
        exp.connect("activate", lambda *_a: self._open_export_dialog())
        self.add_action(exp)

        missing = Gio.SimpleAction.new("find-missing-covers", None)
        missing.connect("activate", lambda *_a: self._find_missing_covers())
        self.add_action(missing)

        find = Gio.SimpleAction.new("find", None)
        find.connect("activate", lambda *_a: self.search_toggle_btn.set_active(
            not self.search_toggle_btn.get_active()))
        self.add_action(find)

        sort_mode = Gio.SimpleAction.new_stateful(
            "sort-mode", GLib.VariantType.new("s"), GLib.Variant("s", self._sort))
        sort_mode.connect("activate", self._on_sort_mode)
        self.add_action(sort_mode)

        # Per-book actions, shared by the detail "⋮" menu and the grid's
        # right-click context menu. Each carries the target book id.
        status_act = Gio.SimpleAction.new("book-status", GLib.VariantType.new("(xs)"))
        status_act.connect("activate", self._act_book_status)
        self.add_action(status_act)
        for name, cb in (("book-cover", self._act_book_cover),
                         ("book-find-cover", self._act_book_find_cover),
                         ("book-link", self._act_book_link),
                         ("book-abandon", self._act_book_abandon),
                         ("book-remove", self._act_book_remove)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("x"))
            act.connect("activate", cb)
            self.add_action(act)

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
        info panel is a fixed 350px floating panel to the paper's right."""
        width, height = self._surface_width, self._surface_height
        if width <= 0 or height <= 0:
            return
        margin_x = max(SPACE_L, round(width * 0.05))
        revealed = self.info_revealer.get_reveal_child()
        if revealed:
            gap = round(width * 0.05)
            ideal_paper = round(width * 0.60)
            centered = (width - ideal_paper - gap - PANEL_WIDTH) // 2
            margin_x = max(margin_x, centered)
            # Pull the panel (and its scrollbar) nearer the window's right edge.
            right_margin = max(SPACE_L, margin_x // 2)
        else:
            gap = 0
            right_margin = margin_x
        # The paper is flush to the window bottom (rounded top corners only);
        # the nav band supplies its top gap, so content_row has no vertical
        # margins. The info panel runs to the window's bottom edge too.
        self.content_row.set_margin_start(margin_x)
        self.content_row.set_margin_end(right_margin)
        self.content_row.set_margin_top(0)
        self.content_row.set_margin_bottom(0)
        self.nav_row.set_margin_start(margin_x)
        self.nav_row.set_margin_end(right_margin + (gap + PANEL_WIDTH if revealed else 0))
        self.info_panel.set_size_request(PANEL_WIDTH if revealed else 0, -1)
        self.info_revealer.set_margin_start(gap)
        self.info_revealer.set_margin_bottom(0)

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
        idx = min(range(len(COVER_SIZES)),
                  key=lambda i: abs(COVER_SIZES[i] - self._cover_w))
        self._cover_w = COVER_SIZES[idx]
        self.cover_scale.get_adjustment().set_value(idx)
        self.cover_scale.connect("value-changed", self._on_cover_size_changed)

    def _on_cover_size_changed(self, scale):
        idx = max(0, min(len(COVER_SIZES) - 1, int(round(scale.get_value()))))
        new_w = COVER_SIZES[idx]
        if new_w == self._cover_w:
            return
        self._cover_w = new_w
        self.settings.set_int("cover-size", new_w)
        if self._cover_size_timer:
            GLib.source_remove(self._cover_size_timer)
        self._cover_size_timer = GLib.timeout_add(60, self._apply_cover_size)

    def _apply_cover_size(self):
        self._cover_size_timer = 0
        self._reload_grid()  # fresh Book objects force every tile to rebind
        return False

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
        # The card hugs the cover width and centres in its (equal-width) grid
        # column, so the title/author sit exactly under the cover.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      halign=Gtk.Align.CENTER,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.add_css_class("card-box")
        box.set_cursor(POINTER_CURSOR)
        cover = Cover("", width=self._cover_w)
        cover.add_css_class("card-cover")
        cover.set_halign(Gtk.Align.FILL)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, wrap=True, lines=2,
                          css_classes=["card-title"])
        title.set_valign(Gtk.Align.START)
        author = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                           css_classes=["card-subtitle"])
        box.append(cover)
        box.append(title)
        box.append(author)
        box.cover, box.title, box.author = cover, title, author
        box._book_id = None
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_card_secondary, box)
        box.add_controller(secondary)
        return box

    def _on_card_secondary(self, gesture, _n, x, y, box):
        if box._book_id is None:
            return
        popover = Gtk.PopoverMenu.new_from_model(self._book_menu(box._book_id))
        popover.set_parent(box)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _bind_card(self, item):
        book = item.get_item()
        box = item.get_child()
        if not hasattr(box, "cover"):
            box = self._card_widget()
            item.set_child(box)
        box._book_id = book.id
        # Card width == cover width, so the title/author never exceed it.
        w = self._cover_w
        chars = max(8, (w - 4) // 8)
        box.set_size_request(w, -1)
        box.cover.set_size(w)
        box.title.set_max_width_chars(chars)
        box.author.set_max_width_chars(chars)
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
                 rating=r["rating"], olid=r["olid"], isbn=r["isbn"], notes=r["notes"],
                 description=r["description"], tags=r["tags"],
                 current_page=r["current_page"], date_started=r["date_started"],
                 date_finished=r["date_finished"])
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
        if mode == "finished":
            # Most recently finished first; books without a finish date last.
            return sorted(books, key=lambda b: (b.date_finished or ""), reverse=True)
        return books  # "recent" — library order is already newest-first

    @staticmethod
    def _matches_query(book, q):
        """Library search matches a book by title, author, or any of its tags."""
        return (q in book.title.lower()
                or q in book.author.lower()
                or q in (book.tags or "").lower())

    def _apply_filter(self):
        q = self._search_query
        visible = []
        for b in self._sorted(self._books_all):
            if b.status != self.shelf:
                continue
            if q and not self._matches_query(b, q):
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

    def _schedule_grid_refresh(self):
        """Coalesce grid rebuilds when many covers land at once (bulk import)."""
        if self._grid_refresh_timer:
            return
        self._grid_refresh_timer = GLib.timeout_add(400, self._do_grid_refresh)

    def _do_grid_refresh(self):
        self._grid_refresh_timer = 0
        self._reload_grid()
        return False

    def _select_tab(self, shelf):
        self.shelf = shelf
        for key, btn in self._tab_buttons.items():
            if key == shelf:
                btn.add_css_class("tab-active")
            else:
                btn.remove_css_class("tab-active")
        self._close_detail()
        self.settings.set_string("last-shelf", shelf)
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

    def _open_book(self, book_id):
        row = lib.get_book(self.con, book_id)
        if not row:
            return
        self._loading_detail = True
        self._detail_book_id = book_id

        child = self.detail_cover_slot.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.detail_cover_slot.remove(child)
            child = nxt
        self.detail_cover_slot.append(self._build_detail_cover(row))
        self.detail_more_btn.set_menu_model(self._book_menu(book_id, include_shelves=False))

        self.detail_title.set_label(row["title"])
        self.detail_author.set_label(row["author"] or "Unknown author")
        self._refresh_date_button(row)
        self.detail_pages.set_label(str(row["pages"]) if row["pages"] else "—")
        self._refresh_progress(row)

        self._render_status_control(row["status"])
        self._render_stars(row["rating"])
        self._refresh_tags(row)
        self._show_summary(row)

        self.info_revealer.set_reveal_child(True)
        self._apply_layout_metrics()
        self._loading_detail = False

    def _build_detail_cover(self, row):
        """The detail cover with a close 'X' that appears on hover to dismiss
        the info panel."""
        self._detail_cover = Cover(row["title"][:18] if not row["cover_path"] else "",
                                   width=DETAIL_COVER_W)
        self._detail_cover.add_css_class("detail-cover")
        self._detail_cover.set_path(row["cover_path"] or None)

        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER)
        overlay.add_css_class("cover-wrap")
        overlay.set_child(self._detail_cover)

        close = Gtk.Button(icon_name="window-close-symbolic",
                           halign=Gtk.Align.END, valign=Gtk.Align.START,
                           margin_top=8, margin_end=8, tooltip_text="Close")
        close.add_css_class("cover-close-btn")
        close.set_cursor(POINTER_CURSOR)
        close.connect("clicked", lambda *_: self._close_detail())
        overlay.add_overlay(close)
        return overlay

    def _close_detail(self):
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

    # ---------- reading dates (editable) ----------

    @staticmethod
    def _value_label(text):
        """A right-aligned detail value that stays on one line while it fits and
        only wraps when it genuinely can't (rather than at a fixed char count),
        so long dates/tags stay readable without breaking early."""
        return Gtk.Label(label=text, xalign=1, wrap=True,
                         wrap_mode=Pango.WrapMode.WORD_CHAR,
                         justify=Gtk.Justification.RIGHT,
                         css_classes=["detail-val"])

    def _refresh_date_button(self, row):
        label = self._value_label(self._format_dates(row))
        self.detail_date_btn.set_child(label)
        self.detail_date_btn.set_popover(self._build_date_editor(row["id"], row))

    def _build_date_editor(self, book_id, row):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        pickers = {}

        def refresh_ranges():
            start = pickers["date_started"].get_selected()
            end = pickers["date_finished"].get_selected()
            for picker in pickers.values():
                picker.set_range(start, end)

        def on_pick(field, value):
            self._apply_date(book_id, field, value)
            refresh_ranges()

        def on_clear(field):
            pickers[field].clear_selection()
            self._apply_date(book_id, field, None)
            refresh_ranges()

        start_t = self._parse_ymd(row["date_started"])
        finish_t = self._parse_ymd(row["date_finished"])
        for field, title in (("date_started", "Started"), ("date_finished", "Finished")):
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            header = Gtk.Box(spacing=8)
            header.append(Gtk.Label(label=title, xalign=0, hexpand=True,
                                    css_classes=["detail-key"]))
            clear = Gtk.Button(label="Clear", css_classes=["flat", "readmore-link"])
            clear.set_cursor(POINTER_CURSOR)
            clear.connect("clicked", lambda _b, f=field: on_clear(f))
            header.append(clear)
            section.append(header)

            picker = DatePicker(
                initial=self._parse_ymd(row[field]),
                on_selected=lambda v, f=field: on_pick(f, v),
                range_start=start_t, range_end=finish_t)
            pickers[field] = picker
            section.append(picker)
            box.append(section)
        pop.set_child(box)
        return pop

    @staticmethod
    def _parse_ymd(value):
        if not value:
            return None
        try:
            d = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            return None
        return (d.year, d.month, d.day)

    def _apply_date(self, book_id, field, value):
        lib.set_book_date(self.con, book_id, field, value)
        if self._detail_book_id == book_id:
            row = lib.get_book(self.con, book_id)
            if row:
                self.detail_date_btn.set_child(self._value_label(self._format_dates(row)))
        self._reload_grid()

    # ---------- reading progress (current page) ----------

    def _render_progress_face(self, row):
        pages = row["pages"] or 0
        current = row["current_page"] or 0
        if pages > 0:
            current = min(current, pages)
            label = f"{current} / {pages}"
            self.detail_progress_bar.set_fraction(current / pages)
            self.detail_progress_bar.set_visible(True)
        elif current > 0:
            label = f"page {current}"
            self.detail_progress_bar.set_visible(False)
        else:
            label = "Not started"
            self.detail_progress_bar.set_visible(False)
        self.detail_progress_btn.set_child(
            Gtk.Label(label=label, xalign=1, css_classes=["detail-val"]))

    def _refresh_progress(self, row):
        self._render_progress_face(row)
        self.detail_progress_btn.set_popover(self._build_progress_editor(row["id"], row))

    def _build_progress_editor(self, book_id, row):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        pages = row["pages"] or 0
        upper = float(pages if pages > 0 else 100000)
        box.append(Gtk.Label(label="Current page", xalign=0, css_classes=["detail-key"]))

        adj = Gtk.Adjustment(lower=0, upper=upper, step_increment=1, page_increment=10,
                             value=min(row["current_page"] or 0, upper))
        spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0, numeric=True)
        spin.connect("value-changed",
                     lambda s: self._apply_progress(book_id, int(s.get_value())))
        box.append(spin)

        if pages > 0:
            quick = Gtk.Box(spacing=6)
            reset = Gtk.Button(label="Reset", css_classes=["flat", "readmore-link"])
            reset.set_cursor(POINTER_CURSOR)
            reset.connect("clicked", lambda *_: spin.set_value(0))
            done = Gtk.Button(label="Finished", css_classes=["flat", "readmore-link"])
            done.set_cursor(POINTER_CURSOR)
            done.connect("clicked", lambda *_: spin.set_value(pages))
            quick.append(reset)
            quick.append(done)
            box.append(quick)
        pop.set_child(box)
        return pop

    def _apply_progress(self, book_id, page):
        lib.set_progress(self.con, book_id, page)
        if self._detail_book_id == book_id:
            row = lib.get_book(self.con, book_id)
            if row:
                self._render_progress_face(row)  # refresh face without closing the popover

    # ---------- tags / genres ----------

    @staticmethod
    def _normalize_tags(text):
        return ", ".join(t.strip() for t in (text or "").split(",") if t.strip())

    def _render_tags_face(self, tags):
        self.detail_tags_btn.set_child(self._value_label(tags or "Add tags…"))

    def _refresh_tags(self, row):
        self._render_tags_face(row["tags"] or "")
        self.detail_tags_btn.set_popover(self._build_tags_editor(row["id"], row))

    @staticmethod
    def _clear_flow(flow):
        child = flow.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            flow.remove(child)
            child = nxt

    def _build_tags_editor(self, book_id, row):
        """A multi-tag editor: current tags as removable chips, an entry to add
        a new one (Enter), and one-tap chips for tags already used elsewhere."""
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.set_size_request(260, -1)

        tags = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]

        current_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                   max_children_per_line=3, column_spacing=6, row_spacing=6)
        entry = Gtk.Entry(placeholder_text="Add a tag, then press Enter")
        suggest_label = Gtk.Label(label="Tags you've used", xalign=0,
                                  css_classes=["mono-dim"])
        suggest_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                   max_children_per_line=3, column_spacing=6, row_spacing=6)

        def add_tag(tag):
            tag = tag.strip()
            if not tag or tag.lower() in {t.lower() for t in tags}:
                return
            tags.append(tag)
            self._apply_tags(book_id, ", ".join(tags))
            render()

        def remove_tag(tag):
            tags[:] = [t for t in tags if t != tag]
            self._apply_tags(book_id, ", ".join(tags))
            render()

        def render():
            self._clear_flow(current_flow)
            for tag in tags:
                current_flow.append(self._removable_chip(tag, remove_tag))
            current_flow.set_visible(bool(tags))

            self._clear_flow(suggest_flow)
            used = {t.lower() for t in tags}
            suggestions = [t for t in lib.all_tags(self.con) if t.lower() not in used]
            for tag in suggestions:
                chip = Gtk.Button(label=tag, css_classes=["flat", "tag-chip"])
                chip.set_cursor(POINTER_CURSOR)
                chip.connect("clicked", lambda _b, t=tag: add_tag(t))
                suggest_flow.append(chip)
            suggest_label.set_visible(bool(suggestions))
            suggest_flow.set_visible(bool(suggestions))

        def on_activate(e):
            add_tag(e.get_text())
            e.set_text("")

        entry.connect("activate", on_activate)

        box.append(current_flow)
        box.append(entry)
        box.append(suggest_label)
        box.append(suggest_flow)
        render()
        pop.set_child(box)
        return pop

    def _removable_chip(self, tag, on_remove):
        chip = Gtk.Box(spacing=2, css_classes=["tag-chip", "tag-chip-active"],
                       valign=Gtk.Align.CENTER)
        chip.append(Gtk.Label(label=tag, valign=Gtk.Align.CENTER))
        close = Gtk.Button(icon_name="window-close-symbolic",
                           css_classes=["flat", "tag-remove"], valign=Gtk.Align.CENTER,
                           tooltip_text="Remove tag")
        close.set_cursor(POINTER_CURSOR)
        close.connect("clicked", lambda *_: on_remove(tag))
        chip.append(close)
        return chip

    def _apply_tags(self, book_id, text):
        tags = self._normalize_tags(text)
        row = lib.get_book(self.con, book_id)
        if row is None or (row["tags"] or "") == tags:
            return  # unchanged — skip a needless write + grid reload
        lib.set_tags(self.con, book_id, tags)
        if self._detail_book_id == book_id:
            self._render_tags_face(tags)
        self._reload_grid()

    # ---------- summary (from Open Library) ----------

    # Summaries longer than this get truncated to 5 lines with a "Read more".
    _SUMMARY_TRUNCATE_AT = 320

    def _show_summary(self, row):
        desc = (row["description"] or "").strip()
        if desc:
            self._set_summary_text(desc)
        elif row["olid"] or row["isbn"]:
            self._set_summary_text("Loading summary…", placeholder=True)
            self._fetch_summary_async(row["id"], row["olid"], row["isbn"])
        else:
            self._set_summary_text("No summary available.", placeholder=True)

    def _set_summary_text(self, text, placeholder=False):
        self._summary_expanded = False
        self.detail_summary.set_label(text)
        if placeholder or len(text) <= self._SUMMARY_TRUNCATE_AT:
            self.detail_summary.set_lines(-1 if placeholder else 5)
            self.detail_readmore_btn.set_visible(False)
        else:
            self.detail_summary.set_lines(5)
            self.detail_readmore_btn.set_label("Read more")
            self.detail_readmore_btn.set_visible(True)

    def _toggle_readmore(self):
        self._summary_expanded = not self._summary_expanded
        self.detail_summary.set_lines(-1 if self._summary_expanded else 5)
        self.detail_readmore_btn.set_label(
            "Read less" if self._summary_expanded else "Read more")

    def _fetch_summary_async(self, book_id, olid, isbn):
        def work():
            desc = ol.fetch_description(olid=olid or "", isbn=isbn or "")
            GLib.idle_add(self._summary_ready, book_id, desc)

        threading.Thread(target=work, daemon=True).start()

    def _summary_ready(self, book_id, desc):
        if desc:
            lib.set_description(self.con, book_id, desc)
        if self._detail_book_id == book_id:
            if desc:
                self._set_summary_text(desc)
            else:
                self._set_summary_text("No summary available.", placeholder=True)
        return False

    # ---------- change cover ----------

    def _open_cover_picker(self, book_id):
        dialog = Gtk.FileDialog(title="Choose a Cover Image")
        img_filter = Gtk.FileFilter()
        img_filter.set_name("Images")
        img_filter.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(img_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(img_filter)
        dialog.open(self, None, lambda dlg, res: self._on_cover_chosen(dlg, res, book_id))

    def _on_cover_chosen(self, dialog, result, book_id):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        src = gfile.get_path() if gfile is not None else None
        if not src:
            return
        ext = os.path.splitext(src)[1].lower() or ".img"
        dest = lib.COVERS_DIR / f"{book_id}{ext}"
        try:
            shutil.copyfile(src, dest)
        except OSError:
            self._toast("Couldn't set that image as the cover.")
            return
        lib.set_cover(self.con, book_id, str(dest))
        self._reload_grid()
        if self._detail_book_id == book_id and self._detail_cover is not None:
            self._detail_cover.set_placeholder("")
            self._detail_cover.set_path(str(dest))
        self._toast("Cover updated")

    def _update_detail_cover(self, book_id, path):
        """Reflect a freshly set cover in the open detail panel, if it's showing
        this book."""
        if self._detail_book_id == book_id and self._detail_cover is not None:
            self._detail_cover.set_placeholder("")
            self._detail_cover.set_path(path)

    # ---------- find cover online (preview modal) ----------

    _COVER_PREVIEW_W = 116     # candidate cover tile width in the picker
    _COVER_PREVIEW_MAX = 5     # number of options shown

    def _open_cover_search(self, book_id):
        """A modal that previews up to five candidate covers found online and
        lets the user pick the one that fits, rather than auto-taking the first."""
        row = lib.get_book(self.con, book_id)
        if not row:
            return
        dialog = Adw.Dialog()
        dialog.set_title("Choose a Cover")
        dialog.set_content_width(600)
        dialog.set_content_height(480)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        entry = Gtk.SearchEntry(placeholder_text="Search covers by title or author")
        entry.add_css_class("search-entry")
        entry.set_text(f'{row["title"]} {row["author"] or ""}'.strip())
        status_lbl = Gtk.Label(label="Searching for covers…", xalign=0,
                               css_classes=["mono-dim"])
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           max_children_per_line=self._COVER_PREVIEW_MAX,
                           min_children_per_line=2, homogeneous=True,
                           column_spacing=12, row_spacing=12, valign=Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(flow)
        box.append(entry)
        box.append(status_lbl)
        box.append(scroller)
        self._dialog_body(dialog, box)

        state = {"timer": 0, "token": 0,
                 "olid": row["olid"] or "", "isbn": row["isbn"] or ""}

        def run_search():
            state["timer"] = 0
            query = entry.get_text().strip()
            self._clear_flow(flow)
            if not query:
                status_lbl.set_label("Type a title or author to search for covers.")
                return False
            status_lbl.set_label("Searching for covers…")
            state["token"] += 1
            token = state["token"]

            def work():
                candidates = self._gather_cover_candidates(
                    query, state["olid"], state["isbn"])
                preview_dir = lib.CACHE_DIR / "cover_search"
                preview_dir.mkdir(parents=True, exist_ok=True)
                found = []
                for kind, value in candidates:
                    if len(found) >= self._COVER_PREVIEW_MAX:
                        break
                    dest = preview_dir / f"{token}_{len(found)}.jpg"
                    path = None
                    try:
                        if kind == "key":
                            path = ol.download_cover_by_key(
                                dest, olid=value[0], isbn=value[1])
                        elif kind == "id":
                            path = ol.download_cover(value, dest)
                        else:
                            path = googlebooks.download_cover_url(value, dest)
                    except Exception:
                        path = None
                    if path:
                        found.append(path)
                GLib.idle_add(deliver, token, found)

            threading.Thread(target=work, daemon=True).start()
            return False

        def deliver(token, found):
            if token != state["token"]:
                return False
            self._clear_flow(flow)
            if not found:
                status_lbl.set_label("No covers found online.")
                return False
            status_lbl.set_label(
                f"{len(found)} option{'' if len(found) == 1 else 's'} — pick one")
            for path in found:
                flow.append(self._cover_option(book_id, path, dialog))
            return False

        def on_changed(_e):
            if state["timer"]:
                GLib.source_remove(state["timer"])
            state["timer"] = GLib.timeout_add(400, run_search)

        entry.connect("search-changed", on_changed)
        dialog.present(self)
        run_search()

    def _cover_option(self, book_id, path, dialog):
        btn = Gtk.Button(css_classes=["flat", "cover-option"])
        btn.set_cursor(POINTER_CURSOR)
        cover = Cover("", width=self._COVER_PREVIEW_W)
        cover.set_path(path)
        btn.set_child(cover)
        btn.connect("clicked", lambda *_: self._choose_cover(book_id, path, dialog))
        return btn

    def _choose_cover(self, book_id, preview_path, dialog):
        dest = lib.COVERS_DIR / f"{book_id}.jpg"
        try:
            if os.path.abspath(preview_path) != os.path.abspath(dest):
                shutil.copyfile(preview_path, dest)
        except OSError:
            self._toast("Couldn't set that cover.")
            return
        lib.set_cover(self.con, book_id, str(dest))
        self._reload_grid()
        self._update_detail_cover(book_id, str(dest))
        self._toast("Cover updated")
        dialog.close()

    def _gather_cover_candidates(self, query, olid, isbn):
        """Collect candidate covers from Open Library and Google Books, most
        relevant first and de-duplicated. Each entry is a ("kind", value) pair:
        "key" (the book's own OLID/ISBN), "id" (an OL cover id), or "url" (a
        direct image URL). Runs on a worker thread; never raises."""
        candidates, seen = [], set()

        def add(kind, value):
            key = (kind, value)
            if value and key not in seen:
                seen.add(key)
                candidates.append(key)

        # The book's own identifier usually yields its exact edition's cover.
        if olid or isbn:
            add("key", (olid, isbn))
        try:
            for res in ol.search(query)[:8]:
                if res.get("cover_i"):
                    add("id", res["cover_i"])
        except Exception:
            pass
        try:
            for res in googlebooks.search(query)[:8]:
                if res.get("cover_url"):
                    add("url", res["cover_url"])
        except Exception:
            pass
        return candidates

    # ---------- look for missing covers (bulk) ----------

    def _find_missing_covers(self):
        targets = [(r["id"], r["olid"] or "", r["isbn"] or "",
                    f'{r["title"]} {r["author"] or ""}'.strip())
                   for r in lib.books_without_cover(self.con)]
        if not targets:
            self._toast("Every book already has a cover.")
            return
        self._toast(
            f"Looking for {len(targets)} missing "
            f"cover{'' if len(targets) == 1 else 's'}…")

        def work():
            found = 0
            for book_id, olid, isbn, query in targets:
                dest = lib.COVERS_DIR / f"{book_id}.jpg"
                path = None
                try:
                    if olid or isbn:
                        path = ol.download_cover_by_key(dest, olid=olid, isbn=isbn)
                    if not path and query:
                        for res in ol.search(query)[:5]:
                            if res.get("cover_i"):
                                path = ol.download_cover(res["cover_i"], dest)
                            if not path and (res.get("olid") or res.get("isbn")):
                                path = ol.download_cover_by_key(
                                    dest, olid=res.get("olid", ""),
                                    isbn=res.get("isbn", ""))
                            if path:
                                break
                except Exception:
                    path = None
                if path:
                    found += 1
                    GLib.idle_add(self._cover_ready, book_id, path)
                time.sleep(0.2)
            GLib.idle_add(
                self._toast,
                f"Found {found} cover{'' if found == 1 else 's'}" if found
                else "No new covers found online")

        threading.Thread(target=work, daemon=True).start()

    # ---------- link to fetch details (per book) ----------

    def _open_link_dialog(self, book_id):
        row = lib.get_book(self.con, book_id)
        if not row:
            return
        dialog = Adw.Dialog()
        dialog.set_title("Link to Fetch Details")
        dialog.set_content_width(460)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        group = Adw.PreferencesGroup(
            description=("Paste a Google Books link, an ISBN, or a Goodreads link "
                         "to fill in this book's missing details and cover."))
        link_row = Adw.EntryRow(title="Link or ISBN")
        group.add(link_row)
        box.append(group)

        status_lbl = Gtk.Label(xalign=0, wrap=True, css_classes=["mono-dim"],
                               label=f'Filling in gaps for “{row["title"]}”.')
        box.append(status_lbl)

        fetch_btn = Gtk.Button(label="Fetch Details", halign=Gtk.Align.CENTER,
                               sensitive=False, css_classes=["empty-cta"])
        fetch_btn.set_cursor(POINTER_CURSOR)
        box.append(fetch_btn)
        self._dialog_body(dialog, box)

        def run():
            text = link_row.get_text().strip()
            if not text:
                return
            fetch_btn.set_sensitive(False)
            status_lbl.set_label("Fetching…")

            def work():
                try:
                    data = enrich.fetch(text)
                except Exception:
                    data = None
                GLib.idle_add(done, data)

            threading.Thread(target=work, daemon=True).start()

        def done(data):
            if not enrich.has_data(data):
                status_lbl.set_label(
                    "Couldn't find any details for that link. "
                    "Check it and try again.")
                fetch_btn.set_sensitive(True)
                return False
            updated = self._apply_link_data(book_id, data)
            if updated:
                self._toast("Updated " + ", ".join(updated))
            else:
                self._toast("Nothing was missing — no changes made.")
            dialog.close()
            return False

        link_row.connect("changed",
                         lambda r: fetch_btn.set_sensitive(bool(r.get_text().strip())))
        link_row.connect("entry-activated", lambda *_: run())
        fetch_btn.connect("clicked", lambda *_: run())
        dialog.present(self)
        link_row.grab_focus()

    def _apply_link_data(self, book_id, data):
        """Fill in only the fields this book is currently missing from a fetched
        metadata dict. Returns a list of human labels for what changed."""
        row = lib.get_book(self.con, book_id)
        if not row:
            return []
        updated, fields = [], {}

        def blank(col):
            value = row[col]
            return not (str(value).strip() if value is not None else "")

        if blank("author") and data.get("author"):
            fields["author"] = data["author"]
            updated.append("author")
        if not row["year"] and data.get("year"):
            fields["year"] = int(data["year"])
            updated.append("year")
        if not row["pages"] and data.get("pages"):
            fields["pages"] = int(data["pages"])
            updated.append("pages")
        if blank("isbn") and data.get("isbn"):
            fields["isbn"] = data["isbn"]
            updated.append("ISBN")
        if blank("olid") and data.get("olid"):
            fields["olid"] = data["olid"]
            updated.append("catalogue id")
        if blank("description") and data.get("description"):
            fields["description"] = data["description"]
            updated.append("summary")
        if fields:
            lib.update_fields(self.con, book_id, **fields)

        if blank("cover_path"):
            if data.get("cover_i"):
                self._fetch_cover_async(book_id, data["cover_i"])
                updated.append("cover")
            elif data.get("cover_url"):
                self._fetch_cover_url_async(book_id, data["cover_url"])
                updated.append("cover")

        self._reload_grid()
        if self._detail_book_id == book_id:
            self._open_book(book_id)  # rebuild the panel with the new details
        return updated

    # ---------- status / rating / abandon ----------

    def _on_status_clicked(self, _btn, status):
        if self._detail_book_id is None:
            return
        self._change_status(self._detail_book_id, status)

    def _on_star_clicked(self, n):
        if self._detail_book_id is None:
            return
        row = lib.get_book(self.con, self._detail_book_id)
        rating = 0 if row and row["rating"] == n else n
        lib.set_rating(self.con, self._detail_book_id, rating)
        self._render_stars(rating)
        self._reload_grid()

    # ---------- shared per-book actions (detail ⋮ menu + grid right-click) ----------

    def _book_menu(self, book_id, include_shelves=True):
        """Build the per-book actions menu. The detail panel's ⋮ passes
        include_shelves=False because the segmented status control already
        offers Read/Reading/To read right beside it; the grid right-click menu
        keeps them, since it has no status control of its own."""
        row = lib.get_book(self.con, book_id)
        status = row["status"] if row else ""
        menu = Gio.Menu()

        if include_shelves:
            shelves = Gio.Menu()
            for key in STATUS_CONTROL:
                if key == status:
                    continue
                item = Gio.MenuItem.new(f"Mark as {SHELF_LABELS[key]}", None)
                item.set_action_and_target_value(
                    "win.book-status", GLib.Variant("(xs)", (book_id, key)))
                shelves.append_item(item)
            menu.append_section(None, shelves)

        mid = Gio.Menu()
        find_item = Gio.MenuItem.new("Find Cover Online…", None)
        find_item.set_action_and_target_value("win.book-find-cover", GLib.Variant("x", book_id))
        mid.append_item(find_item)
        cover_item = Gio.MenuItem.new("Change Cover…", None)
        cover_item.set_action_and_target_value("win.book-cover", GLib.Variant("x", book_id))
        mid.append_item(cover_item)
        link_item = Gio.MenuItem.new("Link to Fetch Details…", None)
        link_item.set_action_and_target_value("win.book-link", GLib.Variant("x", book_id))
        mid.append_item(link_item)
        if status != "abandoned":
            ab = Gio.MenuItem.new("Mark as Abandoned", None)
            ab.set_action_and_target_value("win.book-abandon", GLib.Variant("x", book_id))
            mid.append_item(ab)
        menu.append_section(None, mid)

        danger = Gio.Menu()
        rm = Gio.MenuItem.new("Remove from Library…", None)
        rm.set_action_and_target_value("win.book-remove", GLib.Variant("x", book_id))
        danger.append_item(rm)
        menu.append_section(None, danger)
        return menu

    def _act_book_status(self, _action, param):
        book_id, status = param.unpack()
        self._change_status(book_id, status)

    def _act_book_abandon(self, _action, param):
        self._change_status(param.unpack(), "abandoned")

    def _act_book_cover(self, _action, param):
        self._open_cover_picker(param.unpack())

    def _act_book_find_cover(self, _action, param):
        self._open_cover_search(param.unpack())

    def _act_book_link(self, _action, param):
        self._open_link_dialog(param.unpack())

    def _act_book_remove(self, _action, param):
        self._confirm_delete(param.unpack())

    # Moving onto a shelf stamps the matching reading date: Reading -> started,
    # Read -> finished.
    _STATUS_DATE_FIELD = {"reading": "date_started", "read": "date_finished"}

    @staticmethod
    def _today():
        return datetime.date.today().isoformat()

    def _change_status(self, book_id, status):
        row = lib.get_book(self.con, book_id)
        if not row or row["status"] == status:
            return
        field = self._STATUS_DATE_FIELD.get(status)
        existing = row[field] if field else None
        if field and existing:
            # A date is already recorded — confirm before overwriting it.
            self._confirm_date_change(book_id, status, field, existing)
        elif field:
            # First time onto this shelf: capture today's date automatically.
            self._commit_status(book_id, status, set_field=field, set_value=self._today())
        else:
            self._commit_status(book_id, status)

    def _confirm_date_change(self, book_id, status, field, existing):
        kind = "start" if field == "date_started" else "finish"
        nice = self._fmt_date(existing) or existing
        dialog = Adw.AlertDialog(
            heading=f"Update {kind} date?",
            body=(f"“{kind.capitalize()} date” is already set to {nice}. "
                  f"Moving this book to {SHELF_LABELS.get(status, status)} can "
                  f"update it to today, or keep the existing date."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("keep", "Keep existing")
        dialog.add_response("update", "Set to today")
        dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("keep")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "update":
                self._commit_status(book_id, status,
                                    set_field=field, set_value=self._today())
            elif response == "keep":
                self._commit_status(book_id, status)
            else:  # cancelled: nothing changed, re-sync the control to the old status
                self._resync_detail(book_id)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _commit_status(self, book_id, status, set_field=None, set_value=None):
        lib.set_status(self.con, book_id, status)
        if set_field is not None:
            lib.set_book_date(self.con, book_id, set_field, set_value)
        if status == "read":
            # A finished book is 100% read: snap progress to its last page so the
            # progress bar and "current / pages" reflect completion.
            row = lib.get_book(self.con, book_id)
            if row and row["pages"] and (row["current_page"] or 0) < row["pages"]:
                lib.set_progress(self.con, book_id, row["pages"])
        self._resync_detail(book_id)
        self._reload_grid()
        self._toast(f"Moved to {SHELF_LABELS.get(status, status)}")

    def _resync_detail(self, book_id):
        row = lib.get_book(self.con, book_id)
        if self._detail_book_id == book_id and row:
            self._render_status_control(row["status"])
            self._refresh_date_button(row)
            self._refresh_progress(row)
            self.detail_more_btn.set_menu_model(
                self._book_menu(book_id, include_shelves=False))

    def _confirm_delete(self, book_id):
        if book_id is None:
            return
        row = lib.get_book(self.con, book_id)
        title = row["title"] if row else "this book"
        dialog = Adw.AlertDialog(
            heading="Remove book?",
            body=f"“{title}” will be removed from your library.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response",
                       lambda _d, r: self._do_delete(book_id) if r == "remove" else None)
        dialog.present(self)

    def _do_delete(self, book_id):
        lib.delete_book(self.con, book_id)
        if self._detail_book_id == book_id:
            self._close_detail()
        self._load_books()
        self._toast("Book removed")

    # ---------- add via Open Library ----------

    @staticmethod
    def _dialog_body(dialog, content):
        """Give an Adw.Dialog a header bar so it shows a visible close button
        (Adw.Dialog adds the close button to a header bar automatically) and its
        title, then set the content below it."""
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(content)
        dialog.set_child(toolbar)

    def _open_add_dialog(self):
        dialog = Adw.Dialog()
        dialog.set_title("Add a Book")
        dialog.set_content_width(480)
        dialog.set_content_height(600)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)

        entry = Gtk.SearchEntry(placeholder_text="Search by title or author")
        entry.add_css_class("search-entry")

        shelf_dd = Gtk.DropDown.new_from_strings([SHELF_LABELS[s] for s in STATUS_CONTROL])
        shelf_dd.set_selected(STATUS_CONTROL.index("want"))
        source_dd = Gtk.DropDown.new_from_strings([name for _k, name, _m in SEARCH_BACKENDS])
        saved_source = self.settings.get_string("search-source")
        source_dd.set_selected(next(
            (i for i, (k, _n, _m) in enumerate(SEARCH_BACKENDS) if k == saved_source), 0))
        source_dd.connect("notify::selected", lambda dd, _p: self.settings.set_string(
            "search-source", SEARCH_BACKENDS[dd.get_selected()][0]))
        shelf_row = Gtk.Box(spacing=8)
        shelf_row.append(Gtk.Label(label="Add to", css_classes=["mono-dim"]))
        shelf_row.append(shelf_dd)
        shelf_row.append(Gtk.Box(hexpand=True))
        shelf_row.append(Gtk.Label(label="Source", css_classes=["mono-dim"]))
        shelf_row.append(source_dd)

        status_lbl = Gtk.Label(label="Type to search for a book.",
                               css_classes=["mono-dim"], xalign=0)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                              css_classes=["boxed-list"])
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(listbox)

        box.append(entry)
        box.append(shelf_row)
        box.append(status_lbl)
        box.append(scroller)
        self._dialog_body(dialog, box)

        state = {"timer": 0, "results": [], "token": 0}

        def run_search():
            state["timer"] = 0
            query = entry.get_text().strip()
            if not query:
                self._clear_listbox(listbox)
                status_lbl.set_label("Type to search for a book.")
                return False
            status_lbl.set_label("Searching…")
            state["token"] += 1
            token = state["token"]
            idx = source_dd.get_selected()
            backend = SEARCH_BACKENDS[idx][2] if 0 <= idx < len(SEARCH_BACKENDS) else ol

            def work():
                try:
                    results = backend.search(query)
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
                status_lbl.set_label("Couldn't reach the book service. Check your connection.")
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
        status = STATUS_CONTROL[idx] if 0 <= idx < len(STATUS_CONTROL) else "want"
        book_id = lib.add_book(
            self.con, title=res["title"], author=res["author"], year=res["year"],
            pages=res["pages"], olid=res["olid"], isbn=res.get("isbn", ""),
            status=status)
        self._toast(f'Added “{res["title"]}”')
        self._load_books()
        dialog.close()

        if res.get("cover_i"):
            self._fetch_cover_async(book_id, res["cover_i"])
        elif res.get("cover_url"):
            self._fetch_cover_url_async(book_id, res["cover_url"])

        # Backends that return a description inline (Google Books) store it now;
        # otherwise fall back to Open Library's lazy lookup by OLID/ISBN.
        if res.get("description"):
            lib.set_description(self.con, book_id, res["description"])
        elif res.get("olid") or res.get("isbn"):
            self._fetch_summary_async(book_id, res.get("olid", ""), res.get("isbn", ""))

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

    def _fetch_cover_url_async(self, book_id, url):
        dest = lib.COVERS_DIR / f"{book_id}.jpg"

        def work():
            try:
                path = googlebooks.download_cover_url(url, dest)
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

    @staticmethod
    def _clear_listbox(listbox):
        child = listbox.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            listbox.remove(child)
            child = nxt

    # ---------- add manually ----------

    def _open_manual_dialog(self):
        dialog = Adw.Dialog()
        dialog.set_title("Add a Book")
        dialog.set_content_width(440)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        group = Adw.PreferencesGroup(
            description="For a book that isn't on Open Library or Google Books.")

        title_row = Adw.EntryRow(title="Title")
        author_row = Adw.EntryRow(title="Author")
        group.add(title_row)
        group.add(author_row)

        year_row = Adw.SpinRow.new_with_range(0, 3000, 1)
        year_row.set_title("Year")
        year_row.set_value(0)
        pages_row = Adw.SpinRow.new_with_range(0, 100000, 1)
        pages_row.set_title("Pages")
        pages_row.set_value(0)
        group.add(year_row)
        group.add(pages_row)

        shelf_row = Adw.ComboRow(
            title="Shelf",
            model=Gtk.StringList.new([SHELF_LABELS[s] for s in STATUS_CONTROL]))
        shelf_row.set_selected(STATUS_CONTROL.index("want"))
        group.add(shelf_row)

        tags_row = Adw.EntryRow(title="Tags (comma-separated)")
        group.add(tags_row)
        box.append(group)

        add_btn = Gtk.Button(label="Add Book", halign=Gtk.Align.CENTER,
                             sensitive=False, css_classes=["empty-cta"])
        add_btn.set_cursor(POINTER_CURSOR)
        title_row.connect("changed",
                          lambda r: add_btn.set_sensitive(bool(r.get_text().strip())))
        add_btn.connect("clicked", lambda *_: self._save_manual(
            dialog, title_row, author_row, year_row, pages_row, shelf_row, tags_row))
        box.append(add_btn)

        self._dialog_body(dialog, box)
        dialog.present(self)
        title_row.grab_focus()

    def _save_manual(self, dialog, title_row, author_row, year_row, pages_row,
                     shelf_row, tags_row):
        title = title_row.get_text().strip()
        if not title:
            return
        idx = shelf_row.get_selected()
        status = STATUS_CONTROL[idx] if 0 <= idx < len(STATUS_CONTROL) else "want"
        book_id = lib.add_book(
            self.con, title=title, author=author_row.get_text().strip(),
            year=int(year_row.get_value()), pages=int(pages_row.get_value()),
            status=status)
        tags = self._normalize_tags(tags_row.get_text())
        if tags:
            lib.set_tags(self.con, book_id, tags)
        self._toast(f'Added “{title}”')
        self._load_books()
        dialog.close()

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

    # ---------- export to CSV ----------

    def _open_export_dialog(self):
        if not self._books_all:
            self._toast("Your library is empty — nothing to export.")
            return
        dialog = Gtk.FileDialog(title="Export Library to CSV")
        dialog.set_initial_name("quill-library.csv")
        csv_filter = Gtk.FileFilter()
        csv_filter.set_name("CSV files")
        csv_filter.add_suffix("csv")
        csv_filter.add_mime_type("text/csv")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(csv_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(csv_filter)
        dialog.save(self, None, self._on_export_file_chosen)

    def _on_export_file_chosen(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return  # dismissed
        path = gfile.get_path() if gfile is not None else None
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                count = csvexport.write_csv(fh, lib.all_books(self.con))
        except OSError:
            self._toast("Couldn't write that file.")
            return
        self._toast(f"Exported {count} book{'' if count == 1 else 's'}")

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
