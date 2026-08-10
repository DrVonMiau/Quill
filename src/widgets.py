"""Reusable widgets: a portrait book-cover tile that shows the cover image
when present and a diagonal-striped placeholder (with a caption) otherwise.

Covers are book-shaped (portrait), so the tile is a fixed width with a 3:2
height. It paints with GTK4's native Snapshot/GSK API — no pycairo needed.
"""
import math

from gi.repository import Graphene, Gsk, Gtk

STRIPE_STEP = 7
STRIPE_WIDTH = 2.4
COVER_RATIO = 1.5  # height / width — standard-ish book proportion


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
        self._picture.set_visible(has_path)
        if has_path:
            self._picture.set_filename(path)
        self._area.set_visible(not has_path)
        self._label.set_visible(not has_path and bool(self._placeholder_text))
        self.queue_allocate()
