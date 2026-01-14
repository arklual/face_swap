from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import delete, func, select

from app.db import AsyncSessionLocal
from app.logger import logger
from app.models import CartItem as CartItemModel
from app.models import Job as JobModel
from app.models import JobArtifact as JobArtifactModel
from app.models import OrderItem as OrderItemModel


@dataclass(frozen=True)
class PurgeCounts:
    jobs: int
    job_artifacts: int
    cart_items: int
    order_items: int


async def _get_counts() -> PurgeCounts:
    async with AsyncSessionLocal() as db:
        jobs = (await db.execute(select(func.count()).select_from(JobModel))).scalar_one()
        job_artifacts = (await db.execute(select(func.count()).select_from(JobArtifactModel))).scalar_one()
        cart_items = (await db.execute(select(func.count()).select_from(CartItemModel))).scalar_one()
        order_items = (await db.execute(select(func.count()).select_from(OrderItemModel))).scalar_one()

        return PurgeCounts(
            jobs=int(jobs),
            job_artifacts=int(job_artifacts),
            cart_items=int(cart_items),
            order_items=int(order_items),
        )


async def purge_jobs(*, yes: bool) -> None:
    before = await _get_counts()
    logger.warning(
        "Purge jobs requested",
        extra={
            "before": {
                "jobs": before.jobs,
                "job_artifacts": before.job_artifacts,
                "cart_items": before.cart_items,
                "order_items": before.order_items,
            }
        },
    )

    if not yes:
        raise SystemExit(
            "Refusing to run without --yes. "
            "This will DELETE ALL jobs and related records (job_artifacts, cart_items, order_items)."
        )

    async with AsyncSessionLocal() as db:
        # Order matters due to FKs:
        # - job_artifacts.job_id -> jobs.job_id
        # - cart_items.personalization_id -> jobs.job_id
        # - order_items.personalization_id -> jobs.job_id
        await db.execute(delete(JobArtifactModel))
        await db.execute(delete(OrderItemModel))
        await db.execute(delete(CartItemModel))
        await db.execute(delete(JobModel))
        await db.commit()

    after = await _get_counts()
    logger.warning(
        "Purge jobs completed",
        extra={
            "after": {
                "jobs": after.jobs,
                "job_artifacts": after.job_artifacts,
                "cart_items": after.cart_items,
                "order_items": after.order_items,
            }
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete ALL jobs and dependent records from the database.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive action (required).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(purge_jobs(yes=bool(args.yes)))


if __name__ == "__main__":
    main()

