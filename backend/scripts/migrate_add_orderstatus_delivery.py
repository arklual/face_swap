from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql


def _get_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")
    # Docker compose uses SQLAlchemy-style URLs like "postgresql+psycopg://...".
    # psycopg expects "postgresql://..." (or a conninfo string).
    if database_url.startswith("postgresql+"):
        database_url = "postgresql://" + database_url.split("://", 1)[1]
    return database_url


def _detect_order_status_enum_name(conn: psycopg.Connection) -> str:
    """
    Detect Postgres enum type name used for OrderStatus.

    We look for an enum type that contains 'PENDING_PAYMENT' label (existing value).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.typname
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE e.enumlabel IN ('PENDING_PAYMENT', 'pending_payment')
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and isinstance(row[0], str) and row[0]:
            return row[0]

    # Fallback to SQLAlchemy default for Enum(OrderStatus) without explicit name
    return "orderstatus"


def main() -> None:
    database_url = _get_database_url()

    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    # Use autocommit mode.
    with psycopg.connect(database_url, autocommit=True) as conn:
        enum_name = _detect_order_status_enum_name(conn)

        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER TYPE {} ADD VALUE IF NOT EXISTS {}").format(
                    sql.Identifier(enum_name),
                    # SQLAlchemy Enum(OrderStatus) persists enum *names* in Postgres by default,
                    # so existing labels are uppercase (e.g. PENDING_PAYMENT, PROCESSING).
                    sql.Literal("DELIVERY"),
                )
            )

    print(f"OK: ensured enum {enum_name!r} has value 'DELIVERY'")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        raise

