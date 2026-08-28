"""Fetch a book's missing metadata from an external reference the user pastes:
a Google Books link (or bare volume id), an ISBN, or a Goodreads link.

Standard-library only (urllib), matching openlibrary.py / googlebooks.py. Every
lookup returns a normalized dict with the same shape as the search backends
(``title``/``author``/``year``/``pages``/``isbn``/``olid``/``cover_i``/
``cover_url``/``description``); fields we couldn't determine come back empty so
the caller only fills in what a book is actually missing. Network failures raise
nothing here — they surface as an empty result the UI reports as "not found".
"""
import html
import json
import re
import urllib.parse
import urllib.request

from . import googlebooks

_UA = "Quill/0.1 (github.com/DrVonMiau/quill; books tracker)"
GB_VOLUME_URL = "https://www.googleapis.com/books/v1/volumes/{}"
GB_SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return urllib.request.urlopen(req, timeout=timeout)


def _empty():
    return {"olid": "", "title": "", "author": "", "year": 0, "pages": 0,
            "cover_i": None, "cover_url": "", "isbn": "", "description": ""}


def _clean_isbn(text):
    """Strip a candidate ISBN to digits (plus a trailing X check digit)."""
    return re.sub(r"[^0-9Xx]", "", text or "").upper()


def classify(text):
    """Work out what kind of reference `text` is.

    Returns ``(kind, value)`` where kind is one of ``"googlebooks"`` (value is a
    volume id), ``"isbn"`` (value is the cleaned ISBN), ``"goodreads"`` (value is
    the URL), ``"search"`` (value is free text to search), or ``""`` for empty.
    """
    text = (text or "").strip()
    if not text:
        return "", ""
    low = text.lower()

    if ("books.google" in low or "googleapis.com/books" in low
            or "play.google.com/store/books" in low or "books/edition/" in low):
        for pattern in (r"[?&]id=([A-Za-z0-9_-]+)",
                        r"/volumes/([A-Za-z0-9_-]+)",
                        r"/books/edition/[^/]+/([A-Za-z0-9_-]+)"):
            m = re.search(pattern, text)
            if m:
                return "googlebooks", m.group(1)

    if "goodreads.com" in low:
        return "goodreads", text

    isbn = _clean_isbn(text)
    if len(isbn) in (10, 13) and isbn == text.replace("-", "").replace(" ", "").upper():
        return "isbn", isbn

    # A bare token with no spaces that looks like a Google Books volume id.
    if " " not in text and re.fullmatch(r"[A-Za-z0-9_-]{8,}", text):
        return "googlebooks", text

    return "search", text


def _from_google_volume(info, isbn_hint=""):
    out = _empty()
    out["title"] = info.get("title") or ""
    out["author"] = ", ".join(info.get("authors") or [])
    out["year"] = googlebooks._year(info.get("publishedDate"))
    out["pages"] = info.get("pageCount") or 0
    out["cover_url"] = googlebooks._cover_url(info.get("imageLinks"))
    out["isbn"] = isbn_hint or googlebooks._isbn(info.get("industryIdentifiers"))
    out["description"] = (info.get("description") or "").strip()
    return out


def fetch_google_volume(vol_id, timeout=15):
    try:
        with _get(GB_VOLUME_URL.format(urllib.parse.quote(vol_id)), timeout) as resp:
            data = json.load(resp)
    except Exception:
        return _empty()
    return _from_google_volume(data.get("volumeInfo", {}))


def fetch_isbn(isbn, timeout=15):
    isbn = _clean_isbn(isbn)
    if not isbn:
        return _empty()
    params = urllib.parse.urlencode({"q": f"isbn:{isbn}"})
    try:
        with _get(f"{GB_SEARCH_URL}?{params}", timeout) as resp:
            data = json.load(resp)
    except Exception:
        return _empty()
    items = data.get("items") or []
    if items:
        return _from_google_volume(items[0].get("volumeInfo", {}), isbn_hint=isbn)
    out = _empty()
    out["isbn"] = isbn
    return out


def fetch_search(query, timeout=15):
    try:
        results = googlebooks.search(query, limit=1, timeout=timeout)
    except Exception:
        return _empty()
    if not results:
        return _empty()
    res = results[0]
    out = _empty()
    out.update({k: res.get(k, out[k]) for k in out})
    return out


# Goodreads retired its public API, so we read the public book page and pull the
# ISBN out of its metadata, then enrich through Google Books by that ISBN.
_GR_ISBN_RE = re.compile(r'"isbn"\s*:\s*"([0-9Xx]+)"')
_GR_ISBN13_RE = re.compile(r'itemprop="isbn"[^>]*>([0-9Xx]+)<')
_GR_META_ISBN_RE = re.compile(
    r'<meta\s+property="books:isbn"\s+content="([0-9Xx]+)"')
_GR_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"')


def fetch_goodreads(url, timeout=15):
    try:
        with _get(url, timeout) as resp:
            page = resp.read().decode("utf-8", "replace")
    except Exception:
        return _empty()

    isbn = ""
    for pattern in (_GR_META_ISBN_RE, _GR_ISBN_RE, _GR_ISBN13_RE):
        m = pattern.search(page)
        if m:
            isbn = _clean_isbn(m.group(1))
            if len(isbn) in (10, 13):
                break
            isbn = ""

    title = ""
    m = _GR_TITLE_RE.search(page)
    if m:
        title = html.unescape(m.group(1)).strip()

    if isbn:
        out = fetch_isbn(isbn, timeout)
        if not out.get("title") and title:
            out["title"] = title
        out["isbn"] = out.get("isbn") or isbn
        return out

    if title:
        out = fetch_search(title, timeout)
        if out.get("title"):
            return out
        empty = _empty()
        empty["title"] = title
        return empty
    return _empty()


def fetch(text, timeout=15):
    """Resolve a pasted reference to a normalized metadata dict (see module
    docstring). Always returns a dict; an unresolved reference returns the empty
    shape so the caller can report "no details found"."""
    kind, value = classify(text)
    if kind == "googlebooks":
        return fetch_google_volume(value, timeout)
    if kind == "isbn":
        return fetch_isbn(value, timeout)
    if kind == "goodreads":
        return fetch_goodreads(value, timeout)
    if kind == "search":
        return fetch_search(value, timeout)
    return _empty()


def has_data(data):
    """True when a fetched dict carries anything worth applying."""
    if not data:
        return False
    return bool(data.get("author") or data.get("year") or data.get("pages")
                or data.get("isbn") or data.get("olid") or data.get("description")
                or data.get("cover_url") or data.get("cover_i"))
