-- Migration: group editions of the same work by language and format
-- Run once against an existing database:
--   psql "$DATABASE_URL" -f scripts/book-ingest/migrations/001_add_work_language_grouping.sql
--
-- Adds extracted columns from Open Library edition JSON so you can query
-- "all editions of Room on the Broom" (work_key) and distinguish language
-- and physical format without digging into raw_response.

BEGIN;

-- ---------------------------------------------------------------------------
-- New columns
-- ---------------------------------------------------------------------------

ALTER TABLE books
    ADD COLUMN IF NOT EXISTS work_key TEXT,
    ADD COLUMN IF NOT EXISTS languages TEXT[],
    ADD COLUMN IF NOT EXISTS primary_language TEXT,
    ADD COLUMN IF NOT EXISTS physical_format TEXT;

COMMENT ON COLUMN books.work_key IS
    'Open Library work key, e.g. /works/OL12345W. Editions of the same book share this.';
COMMENT ON COLUMN books.languages IS
    'ISO 639-2 language codes from edition JSON, e.g. {eng}, {fre}.';
COMMENT ON COLUMN books.primary_language IS
    'First language code from languages; convenience column for grouping/filtering.';
COMMENT ON COLUMN books.physical_format IS
    'Physical format from edition JSON, e.g. paperback, hardcover, board book.';

-- ---------------------------------------------------------------------------
-- Indexes for grouping queries
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_books_work_key
    ON books (work_key)
    WHERE work_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_books_primary_language
    ON books (primary_language)
    WHERE primary_language IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_books_work_language
    ON books (work_key, primary_language)
    WHERE work_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_books_languages_gin
    ON books USING GIN (languages)
    WHERE languages IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Backfill existing rows from raw_response
-- ---------------------------------------------------------------------------

UPDATE books
SET work_key = raw_response #>> '{works,0,key}'
WHERE work_key IS NULL
  AND raw_response #>> '{works,0,key}' IS NOT NULL;

UPDATE books
SET languages = lang.codes
FROM (
    SELECT
        b.isbn13,
        array_agg(DISTINCT substring(elem->>'key' FROM '/languages/(.*)'))
            FILTER (WHERE elem->>'key' ~ '^/languages/')
            AS codes
    FROM books b
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(b.raw_response->'languages') = 'array'
            THEN b.raw_response->'languages'
            ELSE '[]'::jsonb
        END
    ) AS elem
    GROUP BY b.isbn13
) AS lang
WHERE books.isbn13 = lang.isbn13
  AND books.languages IS NULL
  AND lang.codes IS NOT NULL;

UPDATE books
SET primary_language = languages[1]
WHERE primary_language IS NULL
  AND languages IS NOT NULL
  AND array_length(languages, 1) >= 1;

UPDATE books
SET physical_format = raw_response->>'physical_format'
WHERE physical_format IS NULL
  AND raw_response ? 'physical_format'
  AND raw_response->>'physical_format' <> '';

-- ---------------------------------------------------------------------------
-- Views for common grouping queries
-- ---------------------------------------------------------------------------

-- All editions of the same work, summarised by language and format.
CREATE OR REPLACE VIEW work_edition_groups AS
SELECT
    work_key,
    primary_language,
    edition,
    physical_format,
    COUNT(*) AS edition_count,
    array_agg(isbn13 ORDER BY isbn13) AS isbns,
    array_agg(title ORDER BY title) AS titles,
    array_agg(cover_url ORDER BY isbn13) AS cover_urls
FROM books
WHERE work_key IS NOT NULL
GROUP BY work_key, primary_language, edition, physical_format
ORDER BY work_key, primary_language NULLS LAST, physical_format NULLS LAST;

COMMENT ON VIEW work_edition_groups IS
    'Editions grouped by work (same book), language, edition label, and physical format.';

-- One row per work with a roll-up across languages and formats.
CREATE OR REPLACE VIEW works_summary AS
SELECT
    work_key,
    COUNT(*) AS total_editions,
    COUNT(DISTINCT primary_language) AS language_count,
    COUNT(DISTINCT physical_format) AS format_count,
    array_agg(DISTINCT primary_language) FILTER (WHERE primary_language IS NOT NULL)
        AS languages,
    array_agg(DISTINCT physical_format) FILTER (WHERE physical_format IS NOT NULL)
        AS formats,
    array_agg(DISTINCT title ORDER BY title) AS titles,
    MIN(publication_year) AS earliest_year,
    MAX(publication_year) AS latest_year
FROM books
WHERE work_key IS NOT NULL
GROUP BY work_key
ORDER BY total_editions DESC, work_key;

COMMENT ON VIEW works_summary IS
    'High-level roll-up: how many editions/languages/formats per Open Library work.';

COMMIT;

-- ---------------------------------------------------------------------------
-- Example queries (run manually; not executed by this migration)
-- ---------------------------------------------------------------------------
--
-- All editions of one work (e.g. Room on the Broom):
--
--   SELECT isbn13, title, primary_language, edition, physical_format, cover_url
--   FROM books
--   WHERE work_key = '/works/OL82563W'
--   ORDER BY primary_language, physical_format, publication_year;
--
-- Find works where you own multiple editions:
--
--   SELECT * FROM works_summary WHERE total_editions > 1;
--
-- Grouped by language and format within a work:
--
--   SELECT * FROM work_edition_groups
--   WHERE work_key IN (
--       SELECT work_key FROM books WHERE title ILIKE '%room on the broom%'
--   );
