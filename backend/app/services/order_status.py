from __future__ import annotations

from typing import Iterable


ORDER_STATUS_DELIVERY = "delivery"


def compute_order_status(*, base_status: str, item_job_statuses: Iterable[str]) -> str:
    """
    Derive a user-facing order status from the base DB status.

    We keep DB statuses unchanged (no migrations), but can expose a richer state
    in API responses.

    Rules:
    - If base_status != "processing" -> return base_status as-is.
    - If base_status == "processing" and all job statuses are "completed" -> "delivery".
    - Otherwise -> "processing".
    """
    if base_status != "processing":
        return base_status

    statuses = [s for s in item_job_statuses if isinstance(s, str)]
    if statuses and all(s == "completed" for s in statuses):
        return ORDER_STATUS_DELIVERY

    return base_status

