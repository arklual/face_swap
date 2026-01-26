#!/usr/bin/env python3
"""
Update Postgres rows that reference an old S3 bucket.

When switching buckets, we must:
1) Copy objects OLD_BUCKET -> NEW_BUCKET
2) Update DB references (jobs.*, job_artifacts.s3_uri, books.*, book_previews.image_url)

Safety:
- Dry-run by default (omit --run).
"""

import os
import sys
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import create_engine, text


@dataclass(frozen=True)
class Config:
    database_url: str
    from_bucket: str
    to_bucket: str
    run: bool


def _get_kv(name: str) -> Optional[str]:
    prefix = f"--{name}="
    for arg in sys.argv[1:]:
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def _usage() -> str:
    return (
        "Usage:\n"
        "  DATABASE_URL='postgresql+psycopg://user:password@host/db' \\\n"
        "  python backend/scripts/migrate_s3_bucket_uris.py \\\n"
        "    --from-bucket=OLD_BUCKET \\\n"
        "    --to-bucket=NEW_BUCKET \\\n"
        "    [--run]\n"
        "\n"
        "Notes:\n"
        "- Dry-run by default (omit --run)\n"
        "- This script updates DB only; it does NOT copy S3 objects\n"
    )


def _replace_expr(col_sql: str, from_bucket: str, to_bucket: str) -> str:
    # Supported formats (simple string replace):
    # - s3://<bucket>/key
    # - https://storage.yandexcloud.net/<bucket>/key
    # - https://<bucket>.storage.yandexcloud.net/key
    # - https://s3.twcstorage.ru/<bucket>/key
    # - https://<bucket>.s3.twcstorage.ru/key
    return (
        "replace("
        "replace("
        "replace("
        "replace("
        "replace("
        f"{col_sql}, 's3://{from_bucket}/', 's3://{to_bucket}/'"
        f"), 'https://storage.yandexcloud.net/{from_bucket}/', 'https://storage.yandexcloud.net/{to_bucket}/'"
        f"), 'https://{from_bucket}.storage.yandexcloud.net/', 'https://{to_bucket}.storage.yandexcloud.net/'"
        f"), 'https://s3.twcstorage.ru/{from_bucket}/', 'https://s3.twcstorage.ru/{to_bucket}/'"
        f"), 'https://{from_bucket}.s3.twcstorage.ru/', 'https://{to_bucket}.s3.twcstorage.ru/'"
        ")"
    )


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    from_bucket = _get_kv("from-bucket")
    to_bucket = _get_kv("to-bucket")
    run = "--run" in sys.argv[1:]

    if not database_url or not from_bucket or not to_bucket:
        print(_usage(), file=sys.stderr)
        return 2

    cfg = Config(
        database_url=database_url,
        from_bucket=from_bucket,
        to_bucket=to_bucket,
        run=run,
    )

    # Columns that can contain S3 URIs / URLs.
    string_columns: list[tuple[str, str]] = [
        ("jobs", "child_photo_uri"),
        ("jobs", "caption_uri"),
        ("jobs", "result_uri"),
        ("jobs", "avatar_url"),
        ("job_artifacts", "s3_uri"),
        ("books", "hero_image"),
        ("book_previews", "image_url"),
    ]

    # JSON fields that contain URL lists.
    json_columns: list[tuple[str, str]] = [
        ("books", "gallery_images"),
    ]

    like_params = {
        "s3_like": f"%{cfg.from_bucket}%",
    }

    engine = create_engine(cfg.database_url)
    with engine.begin() as conn:
        for table, column in string_columns:
            where_sql = f"{column} is not null and {column} like :s3_like"

            if not cfg.run:
                n = conn.execute(
                    text(f"select count(*) as n from {table} where {where_sql}"),
                    like_params,
                ).scalar_one()
                print(f"[dry-run] {table}.{column}: would update {n} rows")
                continue

            res = conn.execute(
                text(
                    f"update {table} "
                    f"set {column} = {_replace_expr(column, cfg.from_bucket, cfg.to_bucket)} "
                    f"where {where_sql}"
                ),
                like_params,
            )
            print(f"[run] {table}.{column}: updated {res.rowcount} rows")

        for table, column in json_columns:
            where_sql = f"{column} is not null and {column}::text like :s3_like"

            if not cfg.run:
                n = conn.execute(
                    text(f"select count(*) as n from {table} where {where_sql}"),
                    like_params,
                ).scalar_one()
                print(f"[dry-run] {table}.{column}: would update {n} rows")
                continue

            expr = _replace_expr(f"{column}::text", cfg.from_bucket, cfg.to_bucket)
            res = conn.execute(
                text(
                    f"update {table} "
                    f"set {column} = ({expr})::json "
                    f"where {where_sql}"
                ),
                like_params,
            )
            print(f"[run] {table}.{column}: updated {res.rowcount} rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

