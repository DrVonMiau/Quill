# Quill

A calm, offline-first **reading tracker** for GNOME. Keep shelves for the books
you want to read, are reading, and have finished; rate them and jot notes; and
add titles in seconds by searching **Open Library**.

Built with GTK4 + libadwaita + PyGObject, packaged as a Flatpak on the GNOME
runtime. It shares its visual language (grey desktop + paper card + segmented
pill tabs, teal/gold accents) with its sibling app **Lyre**.

## Features
- **Read / Reading / To read** shelves as segmented tabs.
- **Master–detail** layout: a paper card of cover tiles with an info panel that
  slides in when you select a book (cover, status, rating, dates, summary).
- A three-way **status control** to move a book between shelves in place.
- **Add books** by searching Open Library, with cover art fetched automatically.
- **Star ratings** and an autosaving per-book **summary**.
- A **cover-size slider** to scale the grid tiles, plus title/author search.
- Light and dark themes that track the system, in the teal & gold house palette.

## Build & run (Flatpak)
```sh
flatpak-builder --user --install --force-clean _flatpak io.github.drvonmiau.Quill.json
flatpak run io.github.drvonmiau.Quill
```

## Build & run (meson, for development)
```sh
meson setup _build
meson compile -C _build
meson install -C _build      # installs the gschema so settings work
```

## Roadmap ideas
- Google Books as a second search backend (behind the same `openlibrary.search()` shape).
- Manual "add book" entry for titles not on Open Library.
- Reading progress (current page) and reading dates on the detail page.
- Import/export (CSV) and per-book tags/genres.

## Notes on the source layout
| File | Responsibility |
|------|----------------|
| `src/main.py` | App entry / `do_activate`, builds `QuillWindow`. |
| `src/window.py` | The whole UI: shelves, cover grid, book detail, add-via-search dialog. |
| `src/library.py` | SQLite library (books, status, rating, notes). Pure, no GTK. |
| `src/openlibrary.py` | Open Library search + cover download (stdlib only). |
| `src/models.py` | The `Book` GObject model. |
| `src/widgets.py` | The portrait `Cover` tile (image or striped placeholder). |
| `src/window.ui` | GTK template. |
| `src/style.css` | Styling (teal/gold accents). |
| `io.github.drvonmiau.Quill.json` | Flatpak manifest. |

The app icon is the final artwork — a 512×512 PNG at
`data/icons/hicolor/512x512/apps/io.github.drvonmiau.Quill.png`. A monochrome
`-symbolic.svg` in `hicolor/symbolic/apps/` accompanies it for system contexts.
