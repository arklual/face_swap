from app.services.order_status import compute_order_status


def test_compute_order_status_processing_all_completed_becomes_delivery():
    assert (
        compute_order_status(base_status="processing", item_job_statuses=["completed", "completed"])
        == "delivery"
    )


def test_compute_order_status_processing_not_all_completed_stays_processing():
    assert (
        compute_order_status(base_status="processing", item_job_statuses=["postpay_generating", "completed"])
        == "processing"
    )


def test_compute_order_status_non_processing_not_overridden():
    assert (
        compute_order_status(base_status="pending_payment", item_job_statuses=["completed", "completed"])
        == "pending_payment"
    )


def test_compute_order_status_processing_empty_items_stays_processing():
    assert compute_order_status(base_status="processing", item_job_statuses=[]) == "processing"

