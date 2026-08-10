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
_UA = "Quill/0.1 (github.com/DrVonMiau/quill; books tracker)"
_FIELDS = "key,title,author_name,first_publish_year,cover_i,number_of_pages_median,isbn"


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return urllib.request.urlopen(req, timeout=timeout)


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
