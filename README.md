# Quill

A calm, offline-first **reading tracker** for GNOME. Keep shelves for the books
you want to read, are reading, and have finished; rate them and jot notes; and
add titles in seconds by searching **Open Library**.

Built with GTK4 + libadwaita + PyGObject, packaged as a Flatpak on the GNOME
runtime. It shares its visual language (grey desktop + paper card + segmented
pill tabs, teal/gold accents) with its sibling app **Lyre**.

## Features
- **Read / Reading / To read / Abandoned** shelves as segmented tabs, plus a
  **Stats** tab with reading analytics.
- **Master–detail** layout: a paper card of cover tiles with an info panel that
  slides in when you select a book (cover, status, rating, dates, progress,
  tags, summary).
- A three-way **status control** to move a book between shelves in place;
  moving to Reading/Read **captures the start/finish date** automatically, and
  asks before changing a date that's already set.
- **Reading dates** you can edit in a Monday-first calendar that **highlights
  the whole started→finished span**, plus **reading progress** (current page)
  with a slim progress bar.
- **Add books** by searching **Open Library or Google Books** (switchable), or
  **add manually** for titles not in either catalogue. Cover art and summaries
  are fetched automatically.
- **Per-book tags/genres**, editable from the info panel.
- **Import** an existing library from an **Openreads CSV export** and **export**
  your library back to a round-trippable CSV.
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
- Import from **Goodreads** (and other platforms) — the CSV importer already
  handles Openreads; Goodreads' export shape is the next target.
- Browse/filter the library by **tag**.

## Notes on the source layout
| File | Responsibility |
|------|----------------|
| `src/main.py` | App entry / `do_activate`, builds `QuillWindow`. |
| `src/window.py` | The whole UI: shelves, cover grid, book detail, add-via-search dialog. |
| `src/library.py` | SQLite library (books, status, rating, notes, tags, progress, dates). Pure, no GTK. |
| `src/openlibrary.py` | Open Library search + cover/summary download (stdlib only). |
| `src/googlebooks.py` | Google Books search backend (same result shape as Open Library). |
| `src/csvimport.py` | Openreads CSV import parser. |
| `src/csvexport.py` | CSV export writer (round-trips through the importer). |
| `src/analytics.py` | Reading statistics for the Stats tab. |
| `src/models.py` | The `Book` GObject model. |
| `src/widgets.py` | The portrait `Cover` tile, `BarChart`, and Monday-first `DatePicker`. |
| `src/window.ui` | GTK template. |
| `src/style.css` | Styling (teal/gold accents). |
| `io.github.drvonmiau.Quill.json` | Flatpak manifest. |

The app icon is the final artwork — a 512×512 PNG at
`data/icons/hicolor/512x512/apps/io.github.drvonmiau.Quill.png`. A monochrome
`-symbolic.svg` in `hicolor/symbolic/apps/` accompanies it for system contexts.
