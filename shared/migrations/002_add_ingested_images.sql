-- Track processed image file hashes so re-runs skip barcode/OCR for unchanged files.
-- Run automatically by ingest.py on startup, or manually:
--   psql "$DATABASE_URL" -f scripts/book-ingest/migrations/002_add_ingested_images.sql

BEGIN;

CREATE TABLE IF NOT EXISTS ingested_images (
    image_sha256    CHAR(64) PRIMARY KEY,
    source_image    TEXT NOT NULL,
    isbn13          TEXT,
    outcome         TEXT NOT NULL CHECK (outcome IN (
        'inserted', 'skipped_isbn', 'read_failure', 'api_not_found', 'api_error'
    )),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingested_images_isbn13
    ON ingested_images (isbn13)
    WHERE isbn13 IS NOT NULL;

COMMENT ON TABLE ingested_images IS
    'SHA-256 of image file bytes. Skips barcode/OCR on re-runs when file unchanged.';

COMMIT;
