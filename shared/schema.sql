CREATE TABLE IF NOT EXISTS books (
    id              SERIAL PRIMARY KEY,
    isbn13          TEXT NOT NULL UNIQUE,
    isbn10          TEXT,
    title           TEXT,
    subtitle        TEXT,
    authors         TEXT[],
    publishers      TEXT[],
    publish_date    TEXT,
    publication_year INTEGER,
    cover_url       TEXT,
    description     TEXT,
    edition         TEXT,
    raw_response    JSONB NOT NULL,
    source_image    TEXT NOT NULL,
    read_method     TEXT NOT NULL CHECK (read_method IN ('barcode', 'ocr')),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
