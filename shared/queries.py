import psycopg 
from psycopg.rows import dict_row
from typing import Any
from pathlib import Path

# --- Common Queries ---
def insert_book(conn: psycopg.Connection, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO books (
                isbn13, isbn10, title, subtitle, authors, publishers,
                publish_date, publication_year, cover_url, description,
                edition, work_key, languages, primary_language, physical_format,
                raw_response, source_image, read_method, quantity
            ) VALUES (
                %(isbn13)s, %(isbn10)s, %(title)s, %(subtitle)s, %(authors)s,
                %(publishers)s, %(publish_date)s, %(publication_year)s,
                %(cover_url)s, %(description)s, %(edition)s,
                %(work_key)s, %(languages)s, %(primary_language)s, %(physical_format)s,
                %(raw_response)s::jsonb, %(source_image)s, %(read_method)s, 1
            )
            ON CONFLICT (isbn13)
            DO UPDATE SET
                quantity = books.quantity + 1;
            """,
            row,
        )
    conn.commit()



# --- CLI specific Queries ---
def get_processed_image(
    conn: psycopg.Connection, image_hash: str
) -> tuple[str | None, str] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT isbn13, outcome FROM ingested_images WHERE image_sha256 = %s",
            (image_hash,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return row[0], row[1]


def record_processed_image(
    conn: psycopg.Connection,
    image_hash: str,
    source_image: str,
    outcome: str,
    isbn13: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingested_images (image_sha256, source_image, isbn13, outcome)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (image_sha256) DO NOTHING
            """,
            (image_hash, source_image, isbn13, outcome),
        )
    conn.commit()

def isbn_exists(conn: psycopg.Connection, isbn13: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM books WHERE isbn13 = %s", (isbn13,))
        return cur.fetchone() is not None


def ensure_schema(conn: psycopg.Connection) -> None:
    base = Path(__file__).parent
    schema_path = base / "schema.sql"
    migrations_dir = base / "migrations"
    with conn.cursor() as cur:
        cur.execute(schema_path.read_text())
        if migrations_dir.is_dir():
            for migration in sorted(migrations_dir.glob("*.sql")):
                cur.execute(migration.read_text())
    conn.commit()


# --- Web App Queries --- 

def get_all_books(conn: psycopg.Connection) -> list[dict[str, Any]]:
    query = """
        SELECT 
        COALESCE(work_key, isbn13) AS group_key,
        MAX(title) as title,
        MAX(authors) as authors,
        MAX(cover_url) AS primary_cover,
        json_agg(
            json_build_object(
                'isbn13', isbn13,
                'edition', edition,
                'publish_date', publish_date,
                'physical_format', physical_format
            )
        ) AS editions
        FROM books
        GROUP BY COALESCE(work_key, isbn13)
        ORDER BY MAX(publication_year) DESC NULLS LAST;
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query)
        return cur.fetchall()


def get_work_by_key(conn, work_key: str):
    query = """
        SELECT 
        COALESCE(work_key, isbn13) AS group_key,
        MAX(title) as title,
        MAX(authors) as authors,
        MAX(cover_url) AS primary_cover,
        MAX(description) AS description,
        json_agg(
            json_build_object(
                'isbn13', isbn13,
                'edition', edition,
                'publish_date', publish_date,
                'physical_format', physical_format,
                'read_method', read_method,
                'raw_response', raw_response,
                'quantity', quantity
            )
        ) AS editions
        FROM books
        WHERE COALESCE(work_key, isbn13) = %s
        GROUP BY COALESCE(work_key, isbn13);
    """
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, (work_key,)).fetchone()

def get_works_by_author(conn, author_name: str):
    query = """
        SELECT 
        COALESCE(work_key, isbn13) AS group_key,
        MAX(title) as title,
        MAX(authors) as authors,
        MAX(cover_url) AS primary_cover
        FROM books
        WHERE %s = ANY(authors)
        GROUP BY COALESCE(work_key, isbn13)
        ORDER BY MAX(publish_date) DESC;
    """
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, (author_name,)).fetchall()