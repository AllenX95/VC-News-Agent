from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from dateutil import parser as dtparser

from .config import TZ


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "spm",
    "from",
    "share",
    "share_token",
    "fbclid",
    "gclid",
}


def now_bj() -> datetime:
    return datetime.now(TZ)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dtparser.parse(value)
        if parsed.tzinfo:
            return parsed.astimezone(TZ).replace(tzinfo=None)
        return parsed
    except (ValueError, TypeError, OverflowError):
        return None


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    if base_url:
        url = urljoin(base_url, url)
    url = unescape(url.strip())
    parsed = urlparse(url)
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme
    netloc = parsed.netloc.lower()
    if netloc.startswith("m.") and "github.com" not in netloc:
        netloc = netloc[2:]
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if k not in TRACKING_PARAMS]
    path = re.sub(r"/+$", "", parsed.path) or "/"
    return urlunparse((scheme, netloc, path, "", urlencode(query, doseq=True), ""))


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def first_chars(text: str, limit: int = 100) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def fingerprint(text: str) -> str:
    return hashlib.sha256(clean_text(text).lower().encode("utf-8")).hexdigest()


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default

