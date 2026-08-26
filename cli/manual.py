import argparse
import os
import sys

from requests.exceptions import RequestException

from shared.database import get_db_connection
from shared import queries
from cli.core import book_lookup
from cli.core import google_books_api
from cli.core import open_library_api


def main():
    parser = argparse.ArgumentParser(description="Manually fix a known book")
    parser.add_argument("--image", required=True, help="Filename of the source image (e.g. 123.jpg)")
    parser.add_argument("--isbn", required=True, help="The ISBN you're looking to add")
    parser.add_argument(
        "--no-google-books",
        action="store_true",
        help="Don't fall back to Google Books if Open Library doesn't have this ISBN",
    )
    args = parser.parse_args()

    conn = get_db_connection()

    if queries.isbn_exists(conn, args.isbn):
        print(f"Skipped: ISBN {args.isbn} is already in the database")
        sys.exit(0)

    ol_client = open_library_api.ApiClient(verbose=True)
    google_client = None
    if not args.no_google_books:
        google_client = google_books_api.ApiClient(
            verbose=True,
            api_key=os.environ.get("GOOGLE_BOOKS_API_KEY"),
        )

    try:
        result = book_lookup.lookup_book(
            isbn13=args.isbn,
            isbn10=None,
            source_image=args.image,
            read_method="ocr",
            ol_client=ol_client,
            google_client=google_client,
            verbose=True,
        )
    except RequestException as exc:
        print(f"Error: Open Library request failed: {exc}")
        sys.exit(1)

    if result is None:
        sources = "Open Library" if args.no_google_books else "Open Library or Google Books"
        print(f"Error: ISBN {args.isbn} not found on {sources}")
        sys.exit(0)

    db_row, source = result
    queries.insert_book(conn, db_row)
    title = db_row.get("title")
    title_note = f' "{title}"' if title else ""
    source_note = "" if source == "open_library" else " (via Google Books)"
    print(f"Successfully added {args.isbn} from {args.image}{title_note}{source_note}")


if __name__ == "__main__":
    main()