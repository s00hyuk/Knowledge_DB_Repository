"""Extraction stage: turn a source (web URL, PDF, or plain text/file) into a
``RawDocument`` with clean text, a guessed material type, and a content hash.

Kept dependency-light and defensive: every extractor degrades gracefully so a
single malformed source never crashes the worker loop.
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import os
from urllib.parse import urlparse

import requests

from .config import Inbox
from .models import RawDocument

USER_AGENT = "knowledge-db/1.0 (+https://github.com/s00hyuk/Knowledge_DB_Repository)"
_REQUEST_TIMEOUT = 30
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _looks_like_url(source: str) -> bool:
    parsed = urlparse(source.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    tail = os.path.basename(path) or urlparse(url).netloc
    return tail or url


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def extract_pdf_bytes(data: bytes, fallback_title: str) -> RawDocument:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - a bad page must not abort the doc
            continue
    text = "\n\n".join(p.strip() for p in pages if p.strip())

    title = fallback_title
    meta_title = getattr(reader.metadata, "title", None) if reader.metadata else None
    if meta_title:
        title = str(meta_title)

    return RawDocument(
        title=title,
        text=text,
        material_type=Inbox.TYPE_PDF,
        content_hash=content_hash(text),
    )


def extract_pdf_file(path: str) -> RawDocument:
    with open(path, "rb") as fh:
        data = fh.read()
    doc = extract_pdf_bytes(data, fallback_title=os.path.splitext(os.path.basename(path))[0])
    doc.local_path = path
    return doc


# --------------------------------------------------------------------------- #
# Web
# --------------------------------------------------------------------------- #
def extract_web(url: str) -> RawDocument:
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=_REQUEST_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()

    # PDFs served over the web: download and route to the PDF extractor.
    if content_type == "application/pdf" or url.lower().endswith(".pdf"):
        data = _read_capped(resp)
        doc = extract_pdf_bytes(data, fallback_title=_title_from_url(url))
        doc.source_url = url
        return doc

    html = resp.text
    title, text = _parse_html(html, url)
    return RawDocument(
        title=title or _title_from_url(url),
        text=text,
        material_type=Inbox.TYPE_WEB,
        source_url=url,
        content_hash=content_hash(text),
    )


def _read_capped(resp: requests.Response) -> bytes:
    buf = io.BytesIO()
    for chunk in resp.iter_content(chunk_size=65536):
        buf.write(chunk)
        if buf.tell() > _MAX_DOWNLOAD_BYTES:
            raise ValueError("Remote file exceeds the 25MB download cap.")
    return buf.getvalue()


def _parse_html(html: str, url: str) -> tuple[str, str]:
    # Prefer trafilatura for main-content extraction; fall back to BeautifulSoup.
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html, url=url, include_comments=False, include_tables=True
        )
        meta = trafilatura.extract_metadata(html)
        title = getattr(meta, "title", None) if meta else None
        if extracted:
            return (title or "", extracted)
    except Exception:  # noqa: BLE001 - fall through to soup
        pass

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = "\n".join(
        line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
    )
    return title, text


# --------------------------------------------------------------------------- #
# Local files / raw text
# --------------------------------------------------------------------------- #
def extract_file(path: str) -> RawDocument:
    mime, _ = mimetypes.guess_type(path)
    if (mime == "application/pdf") or path.lower().endswith(".pdf"):
        return extract_pdf_file(path)

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return RawDocument(
        title=os.path.splitext(os.path.basename(path))[0],
        text=text,
        material_type=Inbox.TYPE_DOC,
        local_path=path,
        content_hash=content_hash(text),
    )


def extract_text(text: str, title: str | None = None) -> RawDocument:
    text = text.strip()
    if not title:
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "메모")
        title = first_line[:80]
    return RawDocument(
        title=title,
        text=text,
        material_type=Inbox.TYPE_TEXT,
        content_hash=content_hash(text),
    )


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
def extract(source: str) -> RawDocument:
    """Route ``source`` (URL, existing file path, or raw text) to an extractor."""
    source = source.strip()
    if _looks_like_url(source):
        return extract_web(source)
    if os.path.exists(source) and os.path.isfile(source):
        return extract_file(source)
    return extract_text(source)


def extract_from_inbox(
    source_url: str | None,
    local_path: str | None,
    inline_text: str | None,
) -> RawDocument:
    """Extract from whichever field an Inbox row provided (URL > file > text)."""
    if source_url:
        return extract_web(source_url)
    if local_path:
        return extract_file(local_path)
    if inline_text:
        return extract_text(inline_text)
    raise ValueError("Inbox item has no 원문 URL, 원본 파일, nor 요약 text to extract from.")
