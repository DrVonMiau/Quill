"""Google Books search — an alternative catalogue behind the same result shape
as ``openlibrary.search()``, so the add-book flow can swap backends freely.

Standard-library only (urllib), like openlibrary.py. Google Books returns cover
art as direct image URLs (rather than Open Library's numeric cover ids), so this
module also exposes ``download_cover_url()`` for that path.
"""
import json
import urllib.parse
import urllib.request

SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"
_UA = "Quill/0.1 (github.com/DrVonMiau/quill; books tracker)"


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return urllib.request.urlopen(req, timeout=timeout)


def _year(published):
    published = (published or "").strip()
    return int(published[:4]) if published[:4].isdigit() else 0


def _isbn(identifiers):
    """Prefer ISBN-13, fall back to ISBN-10."""
    fallback = ""
    for ident in identifiers or []:
        kind, value = ident.get("type", ""), ident.get("identifier", "")
        if kind == "ISBN_13":
            return value
        if kind == "ISBN_10" and not fallback:
            fallback = value
    return fallback


def _cover_url(image_links):
    if not image_links:
        return ""
    for key in ("thumbnail", "smallThumbnail"):
        url = image_links.get(key)
        if url:
            return url.replace("http://", "https://")
    return ""


def search(query, limit=20, timeout=15):
    """Return a list of result dicts for `query`, matching openlibrary.search()'s
    shape (with `cover_url`/`description` populated instead of `cover_i`). Raises
    on network error; callers run this off the main thread."""
    query = (query or "").strip()
    if not query:
        return []
    params = urllib.parse.urlencode(
        {"q": query, "maxResults": min(limit, 40), "printType": "books"})
    with _get(f"{SEARCH_URL}?{params}", timeout) as resp:
        data = json.load(resp)
    results = []
    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        results.append({
            "olid": "",
            "title": info.get("title") or "Untitled",
            "author": ", ".join(info.get("authors") or []),
            "year": _year(info.get("publishedDate")),
            "pages": info.get("pageCount") or 0,
            "cover_i": None,
            "cover_url": _cover_url(info.get("imageLinks")),
            "isbn": _isbn(info.get("industryIdentifiers")),
            "description": (info.get("description") or "").strip(),
        })
    return results


def download_cover_url(url, dest_path, timeout=20):
    """Fetch a cover from a direct image URL into `dest_path`. Returns the path
    on success, or None when there's no usable image."""
    if not url:
        return None
    try:
        with _get(url, timeout) as resp:
            data = resp.read()
    except Exception:
        return None
    if len(data) < 1000:
        return None
    with open(dest_path, "wb") as fh:
        fh.write(data)
    return str(dest_path)
