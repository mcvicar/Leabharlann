import requests
from requests import Response
from requests.exceptions import RequestException
from typing import Any, Literal
import json
import re
import time
import certifi
from dataclasses import dataclass, field
import isbnlib

OPEN_LIBRARY_BASE = "https://openlibrary.org"
YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
LANGUAGE_KEY_PATTERN = re.compile(r"^/languages/([a-z]{3})$", re.IGNORECASE)
REQUEST_DELAY_SEC = 1.0
MAX_RETRIES = 3

@dataclass
class ApiNotFound:
    source_image: str
    isbn13: str
    isbn10: str | None
    read_method: str
    raw_value: str
    edition_url: str
    search_url: str


@dataclass
class ApiError:
    source_image: str
    isbn13: str
    detail: str

class ApiClient:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "book-ingest-cli/1.0"})
        self.session.verify = certifi.where()
        self.author_cache: dict[str, str] = {}
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < REQUEST_DELAY_SEC:
            time.sleep(REQUEST_DELAY_SEC - elapsed)

    def _request(self, url: str) -> Response:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                response = self.session.get(url, timeout=30)
                self._last_request_at = time.monotonic()
                if response.status_code == 404:
                    return response
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = RequestException(
                        f"HTTP {response.status_code} from {url}"
                    )
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response
            except RequestException as exc:
                last_error = exc
                self._last_request_at = time.monotonic()
                time.sleep(2**attempt)
        raise last_error or RequestException(f"Failed to fetch {url}")

    def fetch_edition(self, isbn13: str) -> dict[str, Any] | None:
        url = f"{OPEN_LIBRARY_BASE}/isbn/{isbn13}.json"
        if self.verbose:
            print(f"  GET {url}")
        response = self._request(url)
        if response.status_code == 404:
            return None
        return response.json()

    def resolve_author_name(self, author_key: str) -> str | None:
        if author_key in self.author_cache:
            return self.author_cache[author_key]

        author_id = author_key.removeprefix("/authors/")
        url = f"{OPEN_LIBRARY_BASE}/authors/{author_id}.json"
        if self.verbose:
            print(f"  GET {url}")
        try:
            response = self._request(url)
        except RequestException:
            return None
        if response.status_code == 404:
            return None

        data = response.json()
        name = data.get("name")
        if isinstance(name, str):
            self.author_cache[author_key] = name
            return name
        return None

def extract_fields(
    edition: dict[str, Any],
    authors: list[str],
    isbn13: str | None,
    isbn10: str | None,
    source_image: str,
    read_method: str,
) -> dict[str, Any]:
    confirmed_isbn13 = resolve_isbn13(isbn13, isbn10)
    publish_date = edition.get("publish_date")
    if isinstance(publish_date, str):
        publish_date_value: str | None = publish_date
    else:
        publish_date_value = None

    publishers_raw = edition.get("publishers", [])
    publishers = (
        [publisher for publisher in publishers_raw if isinstance(publisher, str)]
        if isinstance(publishers_raw, list)
        else []
    )

    cover_url = None
    covers = edition.get("covers")
    if isinstance(covers, list) and covers:
        cover_id = covers[0]
        if isinstance(cover_id, int):
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

    description = edition.get("description")
    if isinstance(description, dict):
        description = description.get("value")
    if not isinstance(description, str):
        description = None

    edition_name = edition.get("edition_name")
    if not isinstance(edition_name, str):
        edition_name = None

    title = edition.get("title")
    if not isinstance(title, str):
        title = None

    subtitle = edition.get("subtitle")
    if not isinstance(subtitle, str):
        subtitle = None

    grouping = extract_grouping_fields(edition)

    return {
        "isbn13": confirmed_isbn13,
        "isbn10": isbn10,
        "title": title,
        "subtitle": subtitle,
        "authors": authors or None,
        "publishers": publishers or None,
        "publish_date": publish_date_value,
        "publication_year": parse_publication_year(publish_date_value),
        "cover_url": cover_url,
        "description": description,
        "edition": edition_name,
        **grouping,
        "raw_response": json.dumps(edition),
        "source_image": source_image,
        "read_method": read_method,
    }

def resolve_isbn13(isbn13: str | None, isbn10: str | None) -> str:
    if isbn13:
        return isbn13

    if isbn10:
        converted_isbn = isbnlib.to_isbn13(isbn10)
        if converted_isbn:
            return converted_isbn

def parse_publication_year(publish_date: str | None) -> int | None:
    if not publish_date:
        return None
    match = YEAR_PATTERN.search(publish_date)
    if not match:
        return None
    return int(match.group(0))


def resolve_authors(edition: dict[str, Any], client: ApiClient) -> list[str]:
    authors: list[str] = []
    for entry in edition.get("authors", []):
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        name = client.resolve_author_name(key)
        authors.append(name or key)
    return authors


def extract_work_key(edition: dict[str, Any]) -> str | None:
    """First Open Library work key linking editions of the same book."""
    works = edition.get("works")
    if not isinstance(works, list):
        return None
    for entry in works:
        if isinstance(entry, dict):
            key = entry.get("key")
            if isinstance(key, str) and key.startswith("/works/"):
                return key
    return None


def extract_language_codes(edition: dict[str, Any]) -> list[str]:
    """ISO 639-2 codes from edition JSON, e.g. /languages/eng -> eng."""
    codes: list[str] = []
    languages = edition.get("languages")
    if not isinstance(languages, list):
        return codes

    for entry in languages:
        if isinstance(entry, dict):
            key = entry.get("key")
            if isinstance(key, str):
                match = LANGUAGE_KEY_PATTERN.match(key)
                if match:
                    codes.append(match.group(1).lower())
                    continue
        if isinstance(entry, str) and len(entry) == 3 and entry.isalpha():
            codes.append(entry.lower())

    seen: set[str] = set()
    unique: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique.append(code)
    return unique


def extract_physical_format(edition: dict[str, Any]) -> str | None:
    value = edition.get("physical_format")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def extract_grouping_fields(edition: dict[str, Any]) -> dict[str, Any]:
    languages = extract_language_codes(edition)
    return {
        "work_key": extract_work_key(edition),
        "languages": languages or None,
        "primary_language": languages[0] if languages else None,
        "physical_format": extract_physical_format(edition),
    }

def open_library_urls(isbn13: str) -> tuple[str, str]:
    edition_url = f"{OPEN_LIBRARY_BASE}/isbn/{isbn13}"
    search_url = f"{OPEN_LIBRARY_BASE}/search?q=isbn:{isbn13}"
    return edition_url, search_url
