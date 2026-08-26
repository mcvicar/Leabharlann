from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import certifi
import requests
from requests import Response
from requests.exceptions import RequestException

from cli.core.open_library_api import parse_publication_year

GOOGLE_BOOKS_BASE = "https://www.googleapis.com/books/v1/volumes"
REQUEST_DELAY_SEC = 1.0
MAX_RETRIES = 3

# Fields worth backfilling from a second source if the primary lookup left
# them empty. Deliberately narrow: things like `edition` or `work_key` have
# no real Google Books equivalent, so we don't try to invent one.
ENRICHABLE_FIELDS = ("cover_url", "description", "subtitle")


@dataclass
class ApiNotFound:
    source_image: str
    isbn13: str
    isbn10: str | None
    read_method: str
    raw_value: str
    search_url: str


@dataclass
class ApiError:
    source_image: str
    isbn13: str
    detail: str


class ApiClient:
    """Talks to the Google Books volumes API.

    Unauthenticated requests are allowed but capped at a low daily quota
    (https://developers.google.com/books/docs/v1/using#APIKey). Pass
    api_key (e.g. from a GOOGLE_BOOKS_API_KEY env var) to raise that quota.
    """

    def __init__(self, verbose: bool = False, api_key: str | None = None) -> None:
        self.verbose = verbose
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "book-ingest-cli/1.0"})
        self.session.verify = certifi.where()
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < REQUEST_DELAY_SEC:
            time.sleep(REQUEST_DELAY_SEC - elapsed)

    def _request(self, params: dict[str, str]) -> Response:
        if self.api_key:
            params = {**params, "key": self.api_key}
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                response = self.session.get(GOOGLE_BOOKS_BASE, params=params, timeout=30)
                self._last_request_at = time.monotonic()
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = RequestException(
                        f"HTTP {response.status_code} from {response.url}"
                    )
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response
            except RequestException as exc:
                last_error = exc
                self._last_request_at = time.monotonic()
                time.sleep(2**attempt)
        raise last_error or RequestException("Failed to fetch from Google Books")

    def fetch_volume(self, isbn13: str) -> dict[str, Any] | None:
        """Return the first matching volume item, or None if Google Books has nothing.

        Unlike Open Library's /isbn/{isbn}.json, Google Books never 404s for
        "not found" — it returns 200 with no `items` key, so that's what we
        check for.
        """
        if self.verbose:
            print(f"  GET {GOOGLE_BOOKS_BASE}?q=isbn:{isbn13}")
        response = self._request({"q": f"isbn:{isbn13}", "maxResults": "1"})
        payload = response.json()
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return None
        item = items[0]
        return item if isinstance(item, dict) else None


def _https(url: str | None) -> str | None:
    """Google Books image links come back over http:// — upgrade to https://."""
    if not url:
        return None
    return url.replace("http://", "https://", 1)


def _best_cover_url(image_links: Any) -> str | None:
    if not isinstance(image_links, dict):
        return None
    for key in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
        if image_links.get(key):
            return _https(image_links[key])
    return None


def _isbn10_from_identifiers(identifiers: Any) -> str | None:
    if not isinstance(identifiers, list):
        return None
    for entry in identifiers:
        if isinstance(entry, dict) and entry.get("type") == "ISBN_10":
            value = entry.get("identifier")
            if isinstance(value, str):
                return value
    return None


def _volume_info(item: dict[str, Any]) -> dict[str, Any]:
    info = item.get("volumeInfo")
    return info if isinstance(info, dict) else {}


def extract_fields(
    item: dict[str, Any],
    isbn13: str,
    isbn10: str | None,
    source_image: str,
    read_method: str,
) -> dict[str, Any]:
    """Map a raw Google Books volume item into the row shape
    open_library_api.extract_fields() produces, so callers (and
    queries.insert_book) can treat both providers identically.
    """
    info = _volume_info(item)

    title = info.get("title")
    if not isinstance(title, str):
        title = None

    subtitle = info.get("subtitle")
    if not isinstance(subtitle, str):
        subtitle = None

    authors_raw = info.get("authors")
    authors = (
        [a for a in authors_raw if isinstance(a, str)]
        if isinstance(authors_raw, list)
        else []
    )

    publisher = info.get("publisher")
    publishers = [publisher] if isinstance(publisher, str) and publisher else None

    publish_date = info.get("publishedDate")
    publish_date_value = publish_date if isinstance(publish_date, str) else None

    description = info.get("description")
    if not isinstance(description, str):
        description = None

    language = info.get("language")
    languages = [language] if isinstance(language, str) and language else None

    resolved_isbn10 = isbn10 or _isbn10_from_identifiers(info.get("industryIdentifiers"))

    return {
        "isbn13": isbn13,
        "isbn10": resolved_isbn10,
        "title": title,
        "subtitle": subtitle,
        "authors": authors or None,
        "publishers": publishers,
        "publish_date": publish_date_value,
        "publication_year": parse_publication_year(publish_date_value),
        "cover_url": _best_cover_url(info.get("imageLinks")),
        "description": description,
        # No Open-Library-style edition_name or cross-edition work_key
        # exists in Google Books data — leave unset rather than guess.
        "edition": None,
        "work_key": None,
        "languages": languages,
        "primary_language": languages[0] if languages else None,
        # Google's `printType` (BOOK/MAGAZINE) is a different concept from
        # Open Library's physical_format (Hardcover/Paperback/...) — don't
        # populate this with a mismatched value.
        "physical_format": None,
        "raw_response": json.dumps(item),
        "source_image": source_image,
        "read_method": read_method,
    }


def missing_enrichment_fields(row: dict[str, Any]) -> list[str]:
    """Which enrichable fields are currently empty on this row."""
    return [field for field in ENRICHABLE_FIELDS if not row.get(field)]


def enrich_row(row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Fill only the currently-empty enrichable fields in `row` from a
    Google Books volume item. Never overwrites data the primary source
    already provided.
    """
    info = _volume_info(item)
    if not info:
        return row

    enriched = dict(row)

    if not enriched.get("cover_url"):
        cover_url = _best_cover_url(info.get("imageLinks"))
        if cover_url:
            enriched["cover_url"] = cover_url

    if not enriched.get("description"):
        description = info.get("description")
        if isinstance(description, str):
            enriched["description"] = description

    if not enriched.get("subtitle"):
        subtitle = info.get("subtitle")
        if isinstance(subtitle, str):
            enriched["subtitle"] = subtitle

    return enriched


def google_books_search_url(isbn13: str) -> str:
    return f"{GOOGLE_BOOKS_BASE}?q=isbn:{isbn13}"