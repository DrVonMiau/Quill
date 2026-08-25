# Quill — Handover for the next Claude instance

Read this first. It captures the working context that isn't obvious from the
code alone: what Quill is, what state it's in, the environment gotchas that will
bite you, and what's left to do.

_Last updated: 2026-08-25, after merging v0.2.0 to `main`._

---

## 1. What Quill is

A calm, offline-first **reading tracker** for GNOME. GTK4 + libadwaita +
PyGObject, packaged as a Flatpak on the GNOME runtime (`org.gnome.Platform` 49).
App id `io.github.drvonmiau.Quill`. Repo: `DrVonMiau/Quill` (all lowercase
`drvonmiau/quill` also works). It shares a visual language (grey desktop + paper
card + segmented tabs, teal/gold accents, IBM Plex fonts) with a sibling app
"Lyre".

Data lives in SQLite under the XDG data dir; cover images cache under the XDG
cache dir. Everything is offline except Open Library / Google Books lookups.

## 2. Current state (READ THIS)

- **`main` is the source of truth** and is at the v0.2.0 release commit
  (`e382a7b` at handover time). It contains the full app.
- **`meson.build` version is `0.2.0`.** The AppStream metainfo
  (`data/io.github.drvonmiau.Quill.metainfo.xml.in`) has a matching `0.2.0`
  `<release>` entry.
- **The v0.2.0 git tag and GitHub Release were NOT created from the sandbox** —
  the agent proxy blocks tag/ref creation (see §3). This is the main open item
  (§7).

### Branch map (remote)
| Branch | Meaning | Action |
|---|---|---|
| `main` | Source of truth, v0.2.0 | keep |
| `claude/figma-design` | The pre-existing branch the v0.2.0 work was based on (the "advanced" redesign). Now fully contained in `main`. | can be deleted |
| `claude/quill-detail-fixes-and-roadmap` | The feature branch this work was developed on; merged to `main`. | can be deleted |
| `claude/referral-composer-design-tokens-rsh6sh` | Misnamed/stale (a prompt was pasted into the wrong chat). Fully contained in `main`. | should be deleted |
| `claude/review-push-main-wxwmf3` | Old, == original scaffold `c0aa42e` | stale |

**History note:** the original repo `main` was a bare scaffold (`c0aa42e`). The
real app lived on `claude/figma-design`, which the user pointed us to. Don't be
confused if you ever see an old, simpler version — that's the pre-figma-design
scaffold, not the current app.

## 3. Environment constraints — these WILL bite you

1. **You cannot run the GTK app here.** No PyGObject `_gi`, no display
   (`DISPLAY`/`WAYLAND_DISPLAY` empty). You can only static-check. See §6 for
   the validation you *can* do. The user builds/runs on their own machine
   ("builder"). Anything runtime-dependent (visual layout, signal behavior) is
   effectively untested until they try it — say so.
2. **The git proxy allows branch *pushes* but blocks tag pushes and ref
   *deletions*** (returns HTTP 403 / "Everything up-to-date"). So from here you
   **can** `git push origin <branch>` but you **cannot** push a tag or run
   `git push origin --delete <branch>`. Hand those to the user, or note that
   publishing a GitHub Release from the UI will create the tag server-side.
3. **The Claude Design MCP (`DesignSync`) can't authenticate here** — it needs
   an interactive `/design-login`. If asked to import a claude.ai/design
   project, you can't fetch it; ask the user to use "Send to Claude Code Web" or
   paste the files.
4. **GitHub MCP has no create-release / create-tag / create-ref-for-tag tool.**
   `create_branch` only makes `refs/heads/*`. So there's no way to publish a
   Release or tag from here — it's a user step.
5. MCP servers (github, Figma) disconnect/reconnect intermittently; tools come
   and go via `ToolSearch`. Don't rely on them being present.

## 4. Repo layout / architecture

Pure-data modules have **no GTK import** (kept unit-testable headlessly).

| File | Responsibility |
|---|---|
| `src/main.py` | App entry / `do_activate`, builds `QuillWindow`, About dialog. |
| `src/window.py` | The whole UI (~1600 lines): titlebar, shelf tabs, cover grid, the floating info panel (master-detail), all dialogs. |
| `src/window.ui` | GTK template for `QuillWindow`. |
| `src/style.css` | All styling. Loaded as a gresource. |
| `src/library.py` | SQLite access. Schema + migrations. **No GTK.** |
| `src/models.py` | `Book` GObject wrapper for the grid's `Gio.ListStore`. |
| `src/openlibrary.py` | Open Library search + cover/summary download. Stdlib only. |
| `src/googlebooks.py` | Google Books search backend, same result dict shape. Stdlib only. |
| `src/csvimport.py` | Openreads CSV import parser. **No GTK.** |
| `src/csvexport.py` | CSV export writer (round-trips through the importer). **No GTK.** |
| `src/analytics.py` | Reading stats. **Currently unused** — kept for the future Stats tab. |
| `src/widgets.py` | `Cover` tile, `BarChart` (unused, kept for Stats), Monday-first `DatePicker`. |
| `data/…gschema.xml` | GSettings schema (theme, window state, sort, cover size, search-source). |
| `data/…metainfo.xml.in` | AppStream metadata + `<releases>`. |
| `io.github.drvonmiau.Quill.json` | Flatpak manifest (bundles IBM Plex fonts). |

**Key window.py structure:** `QuillWindow(Adw.ApplicationWindow)` with
`Gtk.Template.Child()` widgets. Shelf tabs map to statuses in `SHELVES`/
`_tab_buttons`. Selecting a cover reveals a `GtkRevealer` info panel
(`_open_book`), which populates status control, stars, date button, progress,
tags, summary. Per-book actions live in a shared `_book_menu(book_id,
include_shelves=…)` used by both the detail ⋮ menu (`include_shelves=False`) and
the grid right-click (`include_shelves=True`).

### Data model (`books` table)
Columns: `id, olid, isbn, title, author, year, pages, cover_url, cover_path,
status, rating, notes, description, tags, current_page, date_added,
date_started, date_finished`. Statuses: `want`, `reading`, `read`, `abandoned`
(note: the "To read" tab == `want`). Migrations are additive in
`library._MIGRATIONS`; **add new columns there too**, not just in `_SCHEMA`, or
existing databases won't upgrade.

## 5. What v0.2.0 added / changed (this engagement)

Built on top of the figma-design redesign:
- **UI fixes:** ⋮ "more" button flattened to a single box (its GtkMenuButton
  inner toggle was drawing a second box); "Mark as Read/Reading/To read" removed
  from the detail ⋮ menu (kept on grid right-click); book covers → 2px corners;
  new transparent-corner app icon; single hover highlight on the Date/Progress/
  Tags rows (same inner-toggle flattening, `.date-edit > button`).
- **Reading dates:** moving to Reading stamps `date_started`, to Read stamps
  `date_finished` (today, ISO `YYYY-MM-DD`). If that date is already set, an
  `Adw.AlertDialog` confirms (Keep / Set to today / Cancel). `lib.set_status` is
  now status-only; the window drives dates.
- **Calendar range highlight:** `DatePicker` shades days between
  `date_started`→`date_finished` (`.dp-day.in-range`), live-updating as either
  date changes.
- **Reading progress:** `current_page` column; editable popover + slim progress
  bar in the panel.
- **Google Books** backend (`googlebooks.py`), switchable in the Add dialog
  (persisted in `search-source` setting). Covers by image URL
  (`_fetch_cover_url_async`), descriptions inline.
- **Add manually** form (`_open_manual_dialog`).
- **Tags/genres:** `tags` column; multi-tag chip editor (removable chips, add by
  Enter, one-tap suggestions); library search matches tags (`_matches_query`).
- **CSV export** (`csvexport.py`) + menu item; importer extended to read tags/
  current_page/description.
- **Stats tab removed** (button, stack page, and all `_show_stats`/`_stat_*`
  code). `analytics.py` and `widgets.BarChart` were **kept in-tree** for a
  planned return — they're currently unreferenced.
- **Cover reload fix:** `Cover.set_path` now builds a fresh `Gdk.Texture` from
  the file bytes instead of `GtkPicture.set_filename`, which short-circuits on an
  equal path — that was why "Find Cover Online" appeared to do nothing when it
  re-downloaded to the same `{id}.jpg`. Also OL cover URLs use `?default=false`
  and the search fallback tries each result's OLID/ISBN.

## 6. How to validate (no GTK runtime here)

Run these after any change; all should pass:
```sh
python3 -m py_compile src/*.py                                   # syntax
cd src && glib-compile-resources --sourcedir=. \
  --target=/tmp/quill.gresource quill.gresource.xml && cd ..     # validates window.ui XML
glib-compile-schemas --dry-run data/                             # validates gschema
python3 -c "import xml.dom.minidom as m; m.parse('data/io.github.drvonmiau.Quill.metainfo.xml.in')"
```
There is **no GTK CSS validator available** — review `style.css` by hand. GTK CSS
is a limited subset (no custom properties; `@define-color`; `alpha()/shade()`
functions; nodes like `menubutton > button`).

The user builds with:
```sh
flatpak-builder --user --install --force-clean _flatpak io.github.drvonmiau.Quill.json
flatpak run io.github.drvonmiau.Quill
# or dev: meson setup _build && meson compile -C _build && meson install -C _build
```

## 7. Open items / next steps

1. **Publish the v0.2.0 release** (blocked from sandbox). Either the user creates
   a GitHub Release at `https://github.com/DrVonMiau/Quill/releases/new` with tag
   `v0.2.0` targeting `main` (GitHub creates the tag), or locally:
   `git tag -a v0.2.0 -m "Quill 0.2.0" && git push origin v0.2.0`. Release notes
   were drafted in chat (mirror the metainfo `0.2.0` entry).
2. **Delete stale branches** (blocked from sandbox — user runs locally):
   `claude/referral-composer-design-tokens-rsh6sh`,
   `claude/quill-detail-fixes-and-roadmap`, and optionally `claude/figma-design`,
   `claude/review-push-main-wxwmf3`.
3. **Roadmap (README):** Goodreads (and other) CSV import — the importer is
   Openreads-shaped; Goodreads export columns differ (`Exclusive Shelf`, `My
   Rating`, `Date Read`, ISBNs with `="..."` wrapping). Add a format detector or
   a second parser. Also: browse/filter the library by tag.
4. **Future:** re-enable a Stats tab (analytics.py + BarChart are still present).

## 8. Untested / risky areas (couldn't run GTK)

These compiled and were logic-reviewed but never executed — verify visually:
- The confirm-date `Adw.AlertDialog` flow and that Cancel re-syncs the status
  control to the unchanged status.
- `DatePicker` range highlight across month boundaries and the live cross-update
  between the Started/Finished pickers.
- The multi-tag chip editor (FlowBox add/remove, suggestions refresh).
- The progress `Gtk.SpinButton` popover (Reset/Finished buttons fire
  value-changed).
- `Adw.SpinRow`/`Adw.EntryRow`/`Adw.ComboRow` usage in the manual-add dialog.
- The `.date-edit`/`.status-more` inner-toggle flattening rendering as one box
  in the real GTK theme.

## 9. Conventions

- **Colours** (`style.css` `@define-color`): teal `#15aaa8` / light `#50d9d5`
  (interactive accent + selection glow); gold `#b9822e` / light `#dea555`
  (ratings); soft text `#4d7f7c` / dark `#9cc7c3`. Surfaces come from libadwaita
  named colours (`@window_bg_color`, `@card_bg_color`, `@window_fg_color`,
  `@view_bg_color`) so both themes track the system. Dark overrides live under
  `window.dark …` selectors.
- **Fonts:** IBM Plex Sans (body/headings), IBM Plex Mono (labels, captions,
  tabs, metadata). Bundled by the Flatpak manifest.
- **Code style:** match surrounding code — no type hints, docstrings on
  non-trivial methods, private helpers prefixed `_`, pure modules stay GTK-free.
- **Git:** develop on a feature branch, never commit model identity or tool
  chatter into the repo. Don't create PRs unless asked.
- **Threading:** network calls run on `threading.Thread(daemon=True)` and return
  to the main loop via `GLib.idle_add`. Never touch widgets off the main thread.

## 10. User context

- Owner/author: Daniel (`daniel.cl@pm.me`), GitHub `DrVonMiau`.
- Works in a mix of chats; a prompt about a "referral composer" was pasted here
  by mistake (that's why the first branch was misnamed) — ignore it.
- Preference observed: wants a single hover/background highlight (not stacked),
  clean minimal detail panel, and roadmap items delivered.
