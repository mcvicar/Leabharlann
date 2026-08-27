import os
import sys
import psycopg
from dotenv import load_dotenv 
from pathlib import Path 

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"

load_dotenv(dotenv_path=env_path)

def get_db_connection(db_url_override: str | None = None) -> psycopg.Connection:
    """
    Establish PostgreSQL connection with proper error handling.
    
    Args:
        db_url_override: Override the DATABASE_URL if provided
        
    Returns:
        Active database connection
        
    Raises:
        RuntimeError: If no connection string is available
        psycopg.Error: For database connection failures
    """
    database_url = db_url_override or os.environ.get("DATABASE_URL")
    
    if not database_url:
        raise RuntimeError(
            "Required environment variable 'DATABASE_URL' is missing. "
            "Set it in your .env file at the project root."
        )

    try:
        import psycopg
        return psycopg.connect(database_url)
    except Exception as e:
        # Log the error but don't crash
        print(f"Database connection failed: {e}", file=sys.stderr)
        raise RuntimeError("Unable to connect to database") from e