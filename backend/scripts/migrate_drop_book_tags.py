from __future__ import annotations

import os
import sys

import psycopg


def _get_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")
    if database_url.startswith("postgresql+"):
        database_url = "postgresql://" + database_url.split("://", 1)[1]
    return database_url


def main() -> None:
    database_url = _get_database_url()

    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE books DROP COLUMN IF EXISTS tags")

    print("OK: dropped books.tags column (if existed)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        raise
