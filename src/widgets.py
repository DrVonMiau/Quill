"""Reusable widgets: a portrait book-cover tile that shows the cover image
when present and a diagonal-striped placeholder (with a caption) otherwise.

Covers are book-shaped (portrait), so the tile is a fixed width with a 3:2
height. It paints with GTK4's native Snapshot/GSK API — no pycairo needed.
"""
import calendar
import datetime
import math

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango

STRIPE_STEP = 7
STRIPE_WIDTH = 2.4
COVER_RATIO = 1.5  # height / width — standard-ish book proportion

_POINTER = Gdk.Cursor.new_from_name("pointer")


class _StripeArea(Gtk.Widget):
    """Fills its area with a 45° repeating stripe in the widget's CSS color."""

    __gtype_name__ = "QuillStripeArea"

    def do_snapshot(self, snapshot):
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return
        rgba = self.get_color()
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2, height / 2))
        snapshot.rotate(45)
        diag = math.hypot(width, height)
        y = -diag
        while y < diag:
            snapshot.append_color(rgba, Graphene.Rect().init(-diag, y, diag * 2, STRIPE_WIDTH))
            y += STRIPE_STEP
        snapshot.restore()
        snapshot.pop()


class Cover(Gtk.Widget):
    """A portrait cover tile: a Gtk.Picture (content-fit cover, clipped to the
    rounded corners) when a path is set, else a striped placeholder with a
    caption. Manual measure/allocate keeps the width:height locked to a book
    proportion regardless of the image's real aspect."""

    __gtype_name__ = "QuillCover"

    def __init__(self, placeholder_text="", width=132):
        super().__init__()
        self._width = width
        self._placeholder_text = placeholder_text
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("cover")

        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
        self._picture.set_parent(self)

        self._area = _StripeArea()
        self._area.set_parent(self)

        self._label = Gtk.Label(label=placeholder_text or "")
        self._label.add_css_class("cover-caption")
        self._label.set_wrap(True)
        self._label.set_justify(Gtk.Justification.CENTER)
        self._label.set_parent(self)

        self.set_path(None)
        # PyGObject doesn't reliably run do_dispose, so unparent on ::destroy.
        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_args):
        for child in (self._picture, self._area, self._label):
            if child.get_parent() is self:
                child.unparent()

    @property
    def _height(self):
        return int(self._width * COVER_RATIO)

    def do_measure(self, orientation, _for_size):
        size = self._width if orientation == Gtk.Orientation.HORIZONTAL else self._height
        return (size, size, -1, -1)

    def do_size_allocate(self, width, height, _baseline):
        for child in (self._picture, self._area):
            if child.get_visible():
                child.allocate(width, height, -1, None)
        if self._label.get_visible():
            _min, lnat, _b1, _b2 = self._label.measure(Gtk.Orientation.HORIZONTAL, -1)
            label_w = min(lnat, width - 16)
            _m2, hnat, _b3, _b4 = self._label.measure(Gtk.Orientation.VERTICAL, label_w)
            transform = Gsk.Transform.new().translate(
                Graphene.Point().init((width - label_w) / 2, (height - hnat) / 2))
            self._label.allocate(label_w, hnat, -1, transform)

    def set_size(self, width):
        if width != self._width:
            self._width = width
            self.queue_resize()

    def set_placeholder(self, text):
        self._placeholder_text = text
        self._label.set_label(text or "")

    def set_path(self, path):
        has_path = bool(path)
        if has_path:
            # Load a fresh texture from the file's bytes rather than set_filename:
            # GtkPicture short-circuits when handed an equal GFile, so a cover
            # re-downloaded to the same path (e.g. "Find Cover Online") would
            # otherwise keep showing the stale image.
            try:
                self._picture.set_paintable(Gdk.Texture.new_from_filename(path))
            except GLib.Error:
                has_path = False
        else:
            self._picture.set_paintable(None)
        self._picture.set_visible(has_path)
        self._area.set_visible(not has_path)
        self._label.set_visible(not has_path and bool(self._placeholder_text))
        self.queue_allocate()


def _tint(rgba, alpha):
    out = Gdk.RGBA()
    out.red, out.green, out.blue, out.alpha = rgba.red, rgba.green, rgba.blue, alpha
    return out


class BarChart(Gtk.Widget):
    """A minimal vertical bar chart drawn with GSK — no plotting dependency.

    `data` is a list of (label, value). Bars use `accent` (a Gdk.RGBA); the
    value on top and the axis label below are drawn in the widget's own text
    colour. `value_fmt` formats the number shown above each bar.
    """

    __gtype_name__ = "QuillBarChart"

    def __init__(self, data, accent, height=196, value_fmt=None):
        super().__init__()
        self._data = list(data)
        self._accent = accent
        self._height = height
        self._value_fmt = value_fmt or (lambda v: str(v))
        self.add_css_class("bar-chart")
        self.set_hexpand(True)

    def set_data(self, data):
        self._data = list(data)
        self.queue_draw()

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        min_w = max(160, len(self._data) * 16)
        return (min_w, max(min_w, len(self._data) * 42), -1, -1)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        n = len(self._data)
        if n == 0 or w <= 0 or h <= 0:
            return
        fg = self.get_color()
        label_c = _tint(fg, fg.alpha * 0.55)
        value_c = _tint(fg, fg.alpha * 0.85)
        grid_c = _tint(fg, fg.alpha * 0.10)

        max_v = max((v for _, v in self._data), default=0) or 1
        pad_top, pad_bottom, pad_right = 16, 22, 24
        chart_w = max(1.0, w - pad_right)
        chart_h = max(1.0, h - pad_top - pad_bottom)
        baseline = pad_top + chart_h
        slot = chart_w / n
        bar_w = min(slot * 0.6, 40)

        # Horizontal gridlines at 0 / ½ / max, with the max value labelled.
        for frac in (0.0, 0.5, 1.0):
            gy = baseline - chart_h * frac
            snapshot.append_color(grid_c, Graphene.Rect().init(0, gy, w, 1))
        self._text(snapshot, self._value_fmt(max_v), chart_w + pad_right / 2,
                   pad_top - 6, pad_right + 8, label_c, 8)

        show_values = n <= 14
        label_step = max(1, math.ceil(n / 16))
        for i, (label, value) in enumerate(self._data):
            cx = i * slot + slot / 2
            bar_h = chart_h * (value / max_v)
            if value > 0:
                rect = Graphene.Rect().init(cx - bar_w / 2, baseline - bar_h, bar_w, bar_h)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(rect, 3)
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(self._accent, rect)
                snapshot.pop()
                if show_values:
                    self._text(snapshot, self._value_fmt(value), cx,
                               baseline - bar_h - 14, slot, value_c, 8)
            if i % label_step == 0:
                self._text(snapshot, label, cx, baseline + 4, slot, label_c, 8)

    def _text(self, snapshot, text, cx, top, width, color, size,
              align=Pango.Alignment.CENTER):
        layout = self.create_pango_layout(text)
        layout.set_alignment(align)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(int(width * Pango.SCALE))
        font = Pango.FontDescription("IBM Plex Mono")
        font.set_size(size * Pango.SCALE)
        layout.set_font_description(font)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx - width / 2, top))
        snapshot.append_layout(layout, color)
        snapshot.restore()


class DatePicker(Gtk.Box):
    """A compact month calendar whose weeks always start on Monday (GtkCalendar
    can't be forced off the locale's first weekday). Calls `on_selected` with an
    ISO "YYYY-MM-DD" string when a day is picked."""

    __gtype_name__ = "QuillDatePicker"

    _WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")

    def __init__(self, initial=None, on_selected=None,
                 range_start=None, range_end=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("date-picker")
        self._on_selected = on_selected
        self._selected = initial  # (y, m, d) or None
        self._range = (range_start, range_end)  # (y,m,d) tuples or None, inclusive
        base = datetime.date(initial[0], initial[1], 1) if initial \
            else datetime.date.today().replace(day=1)
        self._year, self._month = base.year, base.month

        header = Gtk.Box(spacing=4)
        prev = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["flat", "circular"])
        nxt = Gtk.Button(icon_name="go-next-symbolic", css_classes=["flat", "circular"])
        for b, step in ((prev, -1), (nxt, 1)):
            b.set_cursor(_POINTER)
            b.connect("clicked", lambda _b, s=step: self._shift(s))
        self._title = Gtk.Label(hexpand=True, css_classes=["dp-title"])
        header.append(prev)
        header.append(self._title)
        header.append(nxt)
        self.append(header)

        self._grid = Gtk.Grid(column_homogeneous=True, row_spacing=2, column_spacing=2)
        for i, name in enumerate(self._WEEKDAYS):
            self._grid.attach(Gtk.Label(label=name, css_classes=["dp-weekday"]), i, 0, 1, 1)
        self.append(self._grid)

        self._day_buttons = []
        self._rebuild()

    def get_selected(self):
        return self._selected

    def set_range(self, start, end):
        """Set the (start, end) span to shade, as (y,m,d) tuples or None."""
        self._range = (start, end)
        self._rebuild()

    def clear_selection(self):
        self._selected = None
        self._rebuild()

    @staticmethod
    def _as_date(triple):
        if not triple:
            return None
        try:
            return datetime.date(triple[0], triple[1], triple[2])
        except (TypeError, ValueError):
            return None

    def _shift(self, delta):
        m, y = self._month + delta, self._year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self._year, self._month = y, m
        self._rebuild()

    def _rebuild(self):
        for btn in self._day_buttons:
            self._grid.remove(btn)
        self._day_buttons.clear()
        self._title.set_label(datetime.date(self._year, self._month, 1).strftime("%B %Y"))
        first_weekday = datetime.date(self._year, self._month, 1).weekday()  # Mon=0
        days = calendar.monthrange(self._year, self._month)[1]
        rstart, rend = self._as_date(self._range[0]), self._as_date(self._range[1])
        show_range = rstart is not None and rend is not None and rstart <= rend
        col, row = first_weekday, 1
        for day in range(1, days + 1):
            btn = Gtk.Button(label=str(day), css_classes=["flat", "dp-day"])
            btn.set_cursor(_POINTER)
            btn.connect("clicked", lambda _b, d=day: self._pick(d))
            if self._selected == (self._year, self._month, day):
                btn.add_css_class("selected")
            elif show_range and rstart <= datetime.date(self._year, self._month, day) <= rend:
                # Days between the started and finished dates read as a light band.
                btn.add_css_class("in-range")
            self._grid.attach(btn, col, row, 1, 1)
            self._day_buttons.append(btn)
            col += 1
            if col > 6:
                col, row = 0, row + 1

    def _pick(self, day):
        self._selected = (self._year, self._month, day)
        self._rebuild()
        if self._on_selected:
            self._on_selected(f"{self._year:04d}-{self._month:02d}-{day:02d}")
