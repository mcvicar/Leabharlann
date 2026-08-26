# Book ISBN Ingest CLI
Imagine you ended up with _a lot_ of books. A. Lot. So many that you didn't know what you had, but you knew that you had duplicates and various editions of the same book. That's what happened to me, so I wanted to create a simple inventory to just understand what I had. As any technologist does left to their own devices, I over engineered this thing. 


What this does is reads ISBNs from book barcode photos, fetches edition metadata from [Open Library](https://openlibrary.org/developers/api) and optionally Google Books, and stores the results in a local Postgres `books` table. You can then look at your giant collection of books from a simple web ui. 

**Target platform:** Raspberry Pi (Raspberry Pi OS / Debian). It should also run on macOS and other Linux systems, but I haven't tried.

## Prerequisites

- Raspberry Pi 3/4/5 (or similar) running Raspberry Pi OS Bookworm+ (or Debian 12+)
- Python 3.10+
- Postgres running locally (or reachable over the network)

## Setup

```bash
sudo apt update
sudo apt install -y \
  python3-venv python3-pip \
  libzbar0 zbar-tools \
  tesseract-ocr libtesseract-dev \
  libpq-dev postgresql-client \
  python3-opencv    # optional barcode fallback; would recommend

cd scripts/book-ingest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Update the `.env` file in root folder with `DATABASE_URL`.

### Notes

- **Barcode reading** uses `pyzbar` + `libzbar0` in-process on Linux (fast on a raspberry Pi).
- **OpenCV fallback** is optional. On a raspberry Pi, install via `apt install python3-opencv` rather than pip; `pip install opencv-python-headless` can take 30+ minutes to compile.
- **HEIC images** (iPhone photos) aren't supported, as I couldn't get the `libheif1` library to work on my raspberry Pi. 
- **HTTPS** uses the system trust store via `truststore` (included in requirements).
- **Performance:** expect ~10–15 seconds per book (1 req/sec Open Library rate limit + author lookups). A batch of 20 books takes a few minutes.

## Running the CLI

```bash
source scripts/book-ingest/.venv/bin/activate

# Process images in the images/ folder
python -m cli.ingest --directory ~/your/book/barcodes/ --verbose

# Dry run — read ISBNs and call API without writing to DB
python -m cli.ingest --directory ~/your/book/barcodes/ --verbose --dry-run

# Override database connection
python -m cli.ingest --directory ~/your/book/barcodes/ --database-url postgresql://user:pass@localhost:5432/mydb
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--directory` | `images` | Flat directory of barcode images |
| `--database-url` | `$DATABASE_URL` | Postgres connection string |
| `--dry-run` | off | Skip database writes |
| `--verbose` | off | Per-file progress output |
| `--reprocess-images` | off | Re-read barcode images |
| `--no-google-books` | on | Disable the Google Books fallback for ISBNs Open Library can't find |
| `--enrich-with-google-books` | off | Also query Google Books to fill in cover/description/subtitle when Open Library found the book but left those fields empty |

### Supported image formats

`.jpg`, `.jpeg`, `.png`, `.webp`

### while running
Will look like this

Once complete, you'll get a nice report of what happened.
!(Example of the console report after an ingest run)[/example-images/ingest-summary.png]

If you run the script again on the same directory, if the book has already been ingested it'll skip it and move onto the next one. 

## The web UI
The web UI uses flask, and the same the data pulled from open library.

If you want to run it locally, just use `python -m flask --app web.app run`

I have it set up running under apache on my Pi.

## Database schema

The `books` and `ingested_images` tables should be created automatically on first run. See [`schema.sql`](/shared/schema.sql) for the reference DDL.
