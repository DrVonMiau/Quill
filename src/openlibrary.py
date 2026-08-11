"""Open Library lookups: search for books and download cover art.

Uses only the Python standard library (urllib) so no extra Flatpak deps are
needed — just the `--share=network` permission in the manifest. Google Books
can be added later as an alternative backend behind the same `search()` shape.
"""
import json
import urllib.parse
import urllib.request

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{}-L.jpg"
COVER_BY_KEY_URL = "https://covers.openlibrary.org/b/{}/{}-L.jpg"
OL_BASE = "https://openlibrary.org"
_UA = "Quill/0.1 (github.com/DrVonMiau/quill; books tracker)"
_FIELDS = "key,title,author_name,first_publish_year,cover_i,number_of_pages_median,isbn"


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url, timeout):
    with _get(url, timeout) as resp:
        return json.load(resp)


def search(query, limit=20, timeout=15):
    """Return a list of result dicts for `query`. Raises on network error;
    callers run this off the main thread and surface failures as a toast."""
    query = (query or "").strip()
    if not query:
        return []
    params = urllib.parse.urlencode(
        {"q": query, "limit": limit, "fields": _FIELDS})
    with _get(f"{SEARCH_URL}?{params}", timeout) as resp:
        data = json.load(resp)
    results = []
    for doc in data.get("docs", []):
        isbns = doc.get("isbn") or []
        results.append({
            "olid": (doc.get("key") or "").split("/")[-1],
            "title": doc.get("title") or "Untitled",
            "author": ", ".join(doc.get("author_name") or []),
            "year": doc.get("first_publish_year") or 0,
            "pages": doc.get("number_of_pages_median") or 0,
            "cover_i": doc.get("cover_i"),
            "isbn": isbns[0] if isbns else "",
        })
    return results


def download_cover(cover_i, dest_path, timeout=20):
    """Fetch a cover by its Open Library cover id into `dest_path`.
    Returns the path on success, or None when there's no usable image
    (Open Library serves a tiny blank GIF for missing covers)."""
    if not cover_i:
        return None
    with _get(COVER_URL.format(cover_i), timeout) as resp:
        data = resp.read()
    if len(data) < 1000:
        return None
    with open(dest_path, "wb") as fh:
        fh.write(data)
    return str(dest_path)


def download_cover_by_key(dest_path, olid="", isbn="", timeout=20):
    """Fetch a cover by Open Library id (OLID) or ISBN — the identifiers an
    import carries instead of a numeric cover id. Tries OLID first, then ISBN.
    Returns the path on success, or None when no usable image is found."""
    for kind, value in (("olid", olid), ("isbn", isbn)):
        if not value:
            continue
        try:
            with _get(COVER_BY_KEY_URL.format(kind, value), timeout) as resp:
                data = resp.read()
        except Exception:
            continue
        if len(data) < 1000:
            continue
        with open(dest_path, "wb") as fh:
            fh.write(data)
        return str(dest_path)
    return None


def _normalize_description(value):
    """Open Library descriptions are either a plain string or {"value": str}."""
    if isinstance(value, dict):
        value = value.get("value", "")
    return (value or "").strip()


def fetch_description(olid="", isbn="", timeout=15):
    """Return a book's description/summary from Open Library, or "".

    A book's blurb lives on its Work. Our search flow stores the work id
    (``OL…W``); an import stores an edition id (``OL…M``) or ISBN, so those are
    resolved to their work first. Raises nothing — returns "" on any failure."""
    try:
        olid = (olid or "").strip()
        if olid.endswith("W"):
            work = _get_json(f"{OL_BASE}/works/{olid}.json", timeout)
            return _normalize_description(work.get("description"))

        edition = None
        if olid.endswith("M"):
            edition = _get_json(f"{OL_BASE}/books/{olid}.json", timeout)
        elif isbn:
            edition = _get_json(f"{OL_BASE}/isbn/{isbn}.json", timeout)
        if edition is None:
            return ""

        desc = _normalize_description(edition.get("description"))
        if desc:
            return desc
        works = edition.get("works") or []
        if works:
            work_key = works[0].get("key", "").strip("/")
            if work_key:
                work = _get_json(f"{OL_BASE}/{work_key}.json", timeout)
                return _normalize_description(work.get("description"))
    except Exception:
        return ""
    return ""
