#!/usr/bin/env python3
"""Ingest book metadata from barcode images into Postgres via Open Library."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import truststore
truststore.inject_into_ssl()
from requests.exceptions import RequestException
from dotenv import load_dotenv

from shared.database import get_db_connection
from shared import queries
from cli.core import open_library_api
from cli.core import google_books_api
from cli.core import book_lookup
from cli.core import scanner
from cli.core.open_library_api import ApiNotFound, ApiError
from cli.core.scanner import ReadFailure

@dataclass
class InsertedBook:
    source_image: str
    isbn13: str
    title: str | None
    source: str = "open_library"


@dataclass
class SkippedImage:
    source_image: str
    image_sha256: str
    isbn13: str | None
    prior_outcome: str

@dataclass
class RunResults:
    inserted: list[InsertedBook] = field(default_factory=list)
    skipped_images: list[SkippedImage] = field(default_factory=list)
    read_failures: list[ReadFailure] = field(default_factory=list)
    api_not_found: list[ApiNotFound] = field(default_factory=list)
    api_errors: list[ApiError] = field(default_factory=list)

def running_totals_line(results: RunResults) -> str:
    return (
        f"(running: {len(results.inserted)} inserted, "
        f"{len(results.skipped_images)} image skipped, "
        f"{len(results.read_failures)} read failures, "
        f"{len(results.api_not_found)} API not found)"
    )


def format_read_failure(item: ReadFailure) -> str:
    parts = [f"{item.source_image} — {item.reason}"]
    if item.stages_tried:
        parts.append(f"tried: {', '.join(item.stages_tried)}")
    if item.raw_candidate:
        parts.append(f"candidate: {item.raw_candidate!r}")
    if item.invalid_barcodes:
        parts.append(f"invalid barcodes: {', '.join(item.invalid_barcodes)}")
    if item.ocr_snippet:
        parts.append(f"OCR saw: {item.ocr_snippet!r}")
    if item.detail and item.detail not in parts[0]:
        parts.append(item.detail)
    return " | ".join(parts)


def format_api_not_found(item: ApiNotFound) -> str:
    lines = [
        f"{item.source_image} → {item.isbn13} ({item.read_method}, raw={item.raw_value!r})",
    ]
    if item.isbn10:
        lines.append(f"  isbn10: {item.isbn10}")
    lines.append(f"  edition: {item.edition_url}")
    lines.append(f"  search:  {item.search_url}")
    return "\n".join(lines)


def outcome_one_liner(
    outcome: InsertedBook | SkippedImage | ReadFailure | ApiNotFound | ApiError,
) -> str:
    if isinstance(outcome, InsertedBook):
        title = f' "{outcome.title}"' if outcome.title else ""
        source_note = "" if outcome.source == "open_library" else " [via Google Books]"
        return f"inserted {outcome.isbn13}{title}{source_note}"
    if isinstance(outcome, SkippedImage):
        isbn = outcome.isbn13 or "unknown ISBN"
        return f"image skipped ({isbn}, prior {outcome.prior_outcome})"
    if isinstance(outcome, ReadFailure):
        return f"read failure ({outcome.reason})"
    if isinstance(outcome, ApiNotFound):
        return f"API not found {outcome.isbn13} ({outcome.read_method})"
    return f"API error {outcome.isbn13}"

def outcome_record_key(
    outcome: InsertedBook
    | SkippedImage
    | ReadFailure
    | ApiNotFound
    | ApiError,
) -> tuple[str, str | None]:
    if isinstance(outcome, InsertedBook):
        return "inserted", outcome.isbn13
    if isinstance(outcome, SkippedImage):
        return outcome.prior_outcome, outcome.isbn13
    if isinstance(outcome, ReadFailure):
        return "read_failure", None
    if isinstance(outcome, ApiNotFound):
        return "api_not_found", outcome.isbn13
    return "api_error", outcome.isbn13

def process_image(
    image_path: Path,
    conn: Any,
    client: open_library_api.ApiClient,
    dry_run: bool,
    verbose: bool,
    index: int,
    total: int,
    image_hash: str,
    reprocess_images: bool,
    google_client: google_books_api.ApiClient | None,
    enrich_with_google_books: bool,
) -> (
    InsertedBook
    | SkippedImage
    | ReadFailure
    | ApiNotFound
    | ApiError
):
    prefix = f"[{index}/{total}] {image_path.name}"
    if verbose:
        print(f"{prefix} — processing...")

    if conn is not None and not dry_run and not reprocess_images:
        prior = queries.get_processed_image(conn, image_hash)
        if prior is not None:
            isbn13, prior_outcome = prior
            if verbose:
                print(f"  Image unchanged (sha256={image_hash[:12]}…) — skipping read")
            return SkippedImage(
                source_image=image_path.name,
                image_sha256=image_hash,
                isbn13=isbn13,
                prior_outcome=prior_outcome,
            )

    read_result = scanner.read_isbn(image_path)
    if isinstance(read_result, ReadFailure):
        if verbose:
            print(f"  {format_read_failure(read_result)}")
        return read_result

    if verbose:
        print(
            f"  ISBN {read_result.isbn13} via {read_result.read_method}"
            + (f" (isbn10={read_result.isbn10})" if read_result.isbn10 else "")
            + f" raw={read_result.raw_value!r}"
        )

    try:
        result = book_lookup.lookup_book(
            isbn13=read_result.isbn13,
            isbn10=read_result.isbn10,
            source_image=image_path.name,
            read_method=read_result.read_method,
            ol_client=client,
            google_client=google_client,
            verbose=verbose,
            enrich=enrich_with_google_books,
        )
    except RequestException as exc:
        if verbose:
            print(f"  API error: {exc}")
        return ApiError(
            source_image=image_path.name,
            isbn13=read_result.isbn13,
            detail=str(exc),
        )

    if result is None:
        edition_url, search_url = open_library_api.open_library_urls(read_result.isbn13)
        if verbose:
            print(f"  API not found")
            print(f"  edition: {edition_url}")
            print(f"  search:  {search_url}")
        return ApiNotFound(
            source_image=image_path.name,
            isbn13=read_result.isbn13,
            isbn10=read_result.isbn10,
            read_method=read_result.read_method,
            raw_value=read_result.raw_value,
            edition_url=edition_url,
            search_url=search_url,
        )

    row, source = result

    if verbose:
        sparse_fields = [
            name
            for name in (
                "subtitle",
                "description",
                "edition",
                "cover_url",
                "work_key",
                "primary_language",
                "physical_format",
            )
            if row.get(name) is None
        ]
        if sparse_fields:
            print(f"  Sparse fields: {', '.join(sparse_fields)}")
        print(f"  Title: {row.get('title') or '(none)'}")

    if conn is not None and not dry_run:
        queries.insert_book(conn, row)
        if verbose:
            print("  Inserted")

    return InsertedBook(
        source_image=image_path.name,
        isbn13=read_result.isbn13,
        title=row.get("title"),
        source=source,
    )


def print_summary(results: RunResults) -> None:
    print()
    print("=== Book Ingest Summary ===")
    print(f"Inserted:       {len(results.inserted)}")
    print(f"Image skipped:  {len(results.skipped_images)}   (unchanged file)")
    print(f"Read failures:  {len(results.read_failures)}")
    print(f"API not found:  {len(results.api_not_found)}")
    print(f"API errors:     {len(results.api_errors)}")

    if results.skipped_images:
        print()
        print("Image skipped (unchanged file):")
        for item in results.skipped_images:
            isbn = item.isbn13 or "unknown ISBN"
            print(f"  {item.source_image} → {isbn} (prior {item.prior_outcome})")

    if results.read_failures:
        print()
        print("Read failures:")
        for item in results.read_failures:
            print(f"  {format_read_failure(item)}")

    if results.api_not_found:
        print()
        print("API not found:")
        for item in results.api_not_found:
            print(f"  {format_api_not_found(item)}")

    if results.api_errors:
        print()
        print("API errors:")
        for item in results.api_errors:
            print(f"  {item.source_image} → {item.isbn13} — {item.detail}")

    if results.inserted:
        print()
        print("Inserted:")
        for item in results.inserted:
            title = f'  "{item.title}"' if item.title else ""
            source_note = "" if item.source == "open_library" else "  [Google Books]"
            print(f"  {item.source_image} → {item.isbn13}{title}{source_note}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest book metadata from barcode images via Open Library."
    )
    parser.add_argument(
        "--directory",
        default="images",
        help="Flat directory of barcode images (default: images)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Postgres connection URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read ISBNs and call API without writing to the database",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed per-file steps (ISBN reads, API calls, sparse fields)",
    )
    parser.add_argument(
        "--reprocess-images",
        action="store_true",
        help="Re-read images even when file SHA-256 was processed before",
    )
    parser.add_argument(
        "--no-google-books",
        action="store_true",
        help="Disable the Google Books fallback for ISBNs Open Library can't find",
    )
    parser.add_argument(
        "--enrich-with-google-books",
        action="store_true",
        help="Also query Google Books to fill in cover/description/subtitle "
        "when Open Library found the book but left those fields empty",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    load_dotenv(Path(__file__).with_name(".env"))

    args = parse_args()
    directory = Path(args.directory)
    database_url = args.database_url
    if database_url is None:
        import os
        database_url = os.environ.get("DATABASE_URL")

    if not args.dry_run and not database_url:
        print("Error: DATABASE_URL is required (or use --dry-run).", file=sys.stderr)
        sys.exit(2)

    images = scanner.scan_images(directory)
    if not images:
        print(f"No images found in {directory}", file=sys.stderr)
        sys.exit(2)

    total = len(images)
    print(f"Found {total} image(s) in {directory}")

    results = RunResults()
    client = open_library_api.ApiClient(verbose=args.verbose)

    google_client = None
    if not args.no_google_books:
        import os
        google_client = google_books_api.ApiClient(
            verbose=args.verbose,
            api_key=os.environ.get("GOOGLE_BOOKS_API_KEY"),
        )

    conn = None
    if not args.dry_run:
        conn = get_db_connection(database_url)
        ## queries.ensure_schema(conn)

    try:
        for index, image_path in enumerate(images, start=1):
            image_hash = scanner.image_sha256(image_path)
            outcome = process_image(
                image_path=image_path,
                conn=conn,
                client=client,
                dry_run=args.dry_run,
                verbose=args.verbose,
                index=index,
                total=total,
                image_hash=image_hash,
                reprocess_images=args.reprocess_images,
                google_client=google_client,
                enrich_with_google_books=args.enrich_with_google_books,
            )
            if isinstance(outcome, InsertedBook):
                results.inserted.append(outcome)
            elif isinstance(outcome, SkippedImage):
                results.skipped_images.append(outcome)
            elif isinstance(outcome, ReadFailure):
                results.read_failures.append(outcome)
            elif isinstance(outcome, ApiNotFound):
                results.api_not_found.append(outcome)
            elif isinstance(outcome, ApiError):
                results.api_errors.append(outcome)

            if conn is not None and not args.dry_run and not isinstance(outcome, SkippedImage):
                record_key, isbn13 = outcome_record_key(outcome)
                queries.record_processed_image(
                    conn,
                    image_hash,
                    image_path.name,
                    record_key,
                    isbn13,
                )

            print(f"[{index}/{total}] {image_path.name} — {outcome_one_liner(outcome)}")
            if isinstance(outcome, ReadFailure):
                print(f"  {format_read_failure(outcome)}")
            elif isinstance(outcome, ApiNotFound):
                for line in format_api_not_found(outcome).splitlines():
                    print(f"  {line}")
            print(f"  {running_totals_line(results)}")
    finally:
        if conn is not None:
            conn.close()

    print_summary(results)

    if results.read_failures or results.api_not_found or results.api_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()