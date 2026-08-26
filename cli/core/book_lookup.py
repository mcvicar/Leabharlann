"""Look a book up across providers: Open Library first, Google Books as a
fallback (and optional enrichment source). This is the one place that
combines the two providers, so ingest.py's batch pipeline and manual.py's
single-book fixer both go through the same logic instead of each
reimplementing "try Open Library, then Google Books."
"""

from __future__ import annotations

from typing import Any

from requests.exceptions import RequestException

from cli.core import google_books_api
from cli.core import open_library_api


def lookup_book(
    isbn13: str,
    isbn10: str | None,
    source_image: str,
    read_method: str,
    ol_client: open_library_api.ApiClient,
    google_client: google_books_api.ApiClient | None = None,
    verbose: bool = False,
    enrich: bool = False,
) -> tuple[dict[str, Any], str] | None:
    """Look up a book by ISBN.

    Tries Open Library first. If it has nothing and google_client is
    given, falls back to Google Books. If Open Library *does* have it and
    enrich=True, also queries Google Books to fill in fields Open Library
    left empty (cover_url, description, subtitle) — never overwriting
    anything Open Library already provided.

    Returns (row, source) where row is ready for queries.insert_book() and
    source is "open_library" or "google_books". Returns None if neither
    provider has the book.

    Raises:
        RequestException: if the Open Library lookup itself fails (a
            network/HTTP problem, distinct from a genuine "not found").
            Google Books failures are never raised to the caller — a
            broken fallback or enrichment source just means you get
            whatever Open Library gave you (including nothing).
    """
    edition = ol_client.fetch_edition(isbn13)  # RequestException propagates to caller

    if edition is not None:
        authors = open_library_api.resolve_authors(edition, ol_client)
        row = open_library_api.extract_fields(
            edition=edition,
            authors=authors,
            isbn13=isbn13,
            isbn10=isbn10,
            source_image=source_image,
            read_method=read_method,
        )
        if enrich and google_client is not None:
            row = _enrich_with_google_books(row, isbn13, google_client, verbose)
        return row, "open_library"

    if verbose:
        print("  Not found on Open Library")

    if google_client is None:
        return None

    try:
        volume = google_client.fetch_volume(isbn13)
    except RequestException as exc:
        if verbose:
            print(f"  Google Books fallback error: {exc}")
        return None

    if volume is None:
        if verbose:
            print("  Not found on Google Books either")
        return None

    row = google_books_api.extract_fields(
        item=volume,
        isbn13=isbn13,
        isbn10=isbn10,
        source_image=source_image,
        read_method=read_method,
    )
    if verbose:
        print(f"  Found via Google Books fallback — Title: {row.get('title') or '(none)'}")
    return row, "google_books"


def _enrich_with_google_books(
    row: dict[str, Any],
    isbn13: str,
    google_client: google_books_api.ApiClient,
    verbose: bool,
) -> dict[str, Any]:
    missing = google_books_api.missing_enrichment_fields(row)
    if not missing:
        return row

    try:
        volume = google_client.fetch_volume(isbn13)
    except RequestException as exc:
        if verbose:
            print(f"  Google Books enrichment error: {exc}")
        return row

    if volume is None:
        return row

    enriched = google_books_api.enrich_row(row, volume)
    if verbose:
        filled = [f for f in missing if enriched.get(f) and not row.get(f)]
        if filled:
            print(f"  Enriched from Google Books: {', '.join(filled)}")
    return enriched