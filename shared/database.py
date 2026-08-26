import os
import sys
import psycopg
from dotenv import load_dotenv 
from pathlib import Path 

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"

load_dotenv(dotenv_path=env_path)

def get_db_connection(db_url_override: str | None = None) -> psycopg.Connection: 
    database_url = db_url_override or os.environ.get("DATABASE_URL")

    if not database_url:
        print("Error: DATABASE_URL doesn't seem to be set", file=sys.stderr)
        print("Check the .env file in the root directory of the project or export it directly", file=sys.stderr)
        sys.exit(1)
    try:
        return psycopg.connect(database_url)
    except psycopg.Error as e:
        print(f"Database connection failed: {e}", file=sys.stderr)
        sys.exit(1)