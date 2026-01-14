import pytest
from botocore.exceptions import ClientError

from app.config import settings
from app.models import Job
from app.routes import personalizations as personalizations_module
from fastapi import HTTPException


class DummyS3:
    def __init__(self, head_error=None, head_fn=None):
        self.head_error = head_error
        self.head_fn = head_fn
        self.head_calls = 0
        self.put_calls = []

    def head_object(self, **_kwargs):
        self.head_calls += 1
        if self.head_fn:
            return self.head_fn()
        if self.head_error:
            raise self.head_error
        return {}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        return {}


def make_job() -> Job:
    return Job(
        job_id="job-123",
        user_id="user-123",
        slug="book-slug",
        status="completed",
        child_name="",
        child_age=1,
    )


@pytest.mark.asyncio
async def test_ensure_pdf_in_s3_existing_skips_generation(monkeypatch):
    dummy_s3 = DummyS3()
    monkeypatch.setattr(personalizations_module, "s3", dummy_s3)

    build_calls = {"count": 0}

    def _build_pdf_bytes(_job, _page_nums):
        build_calls["count"] += 1
        return b"pdf"

    monkeypatch.setattr(personalizations_module, "_build_pdf_bytes", _build_pdf_bytes)

    job = make_job()
    key = await personalizations_module._ensure_pdf_in_s3(job, [1, 2])

    assert key == personalizations_module._pdf_s3_key(job.job_id)
    assert build_calls["count"] == 0
    assert dummy_s3.put_calls == []


@pytest.mark.asyncio
async def test_ensure_pdf_in_s3_missing_generates_and_uploads(monkeypatch):
    error = ClientError(
        {
            "Error": {"Code": "404", "Message": "Not Found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        "HeadObject",
    )
    dummy_s3 = DummyS3(head_error=error)
    monkeypatch.setattr(personalizations_module, "s3", dummy_s3)

    build_calls = {"count": 0}

    def _build_pdf_bytes(_job, _page_nums):
        build_calls["count"] += 1
        return b"pdf-bytes"

    monkeypatch.setattr(personalizations_module, "_build_pdf_bytes", _build_pdf_bytes)

    job = make_job()
    key = await personalizations_module._ensure_pdf_in_s3(job, [1, 2])

    assert key == personalizations_module._pdf_s3_key(job.job_id)
    assert build_calls["count"] == 1
    assert len(dummy_s3.put_calls) == 1
    put_kwargs = dummy_s3.put_calls[0]
    assert put_kwargs["Bucket"] == settings.S3_BUCKET_NAME
    assert put_kwargs["Key"] == personalizations_module._pdf_s3_key(job.job_id)
    assert put_kwargs["Body"] == b"pdf-bytes"
    assert put_kwargs["ContentType"] == "application/pdf"
    assert put_kwargs["CacheControl"] == "no-store"


@pytest.mark.asyncio
async def test_wait_for_s3_object_retries_until_ready(monkeypatch):
    calls = {"count": 0}

    def _head_fn():
        calls["count"] += 1
        if calls["count"] < 2:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {}

    dummy_s3 = DummyS3(head_fn=_head_fn)
    monkeypatch.setattr(personalizations_module, "s3", dummy_s3)

    async def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(personalizations_module.asyncio, "sleep", _noop_sleep)

    ready = await personalizations_module._wait_for_s3_object("bucket", "key", attempts=2, delay_seconds=0.0)
    assert ready is True
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_ensure_pdf_in_s3_raises_when_not_ready(monkeypatch):
    error = ClientError(
        {
            "Error": {"Code": "404", "Message": "Not Found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        "HeadObject",
    )
    dummy_s3 = DummyS3(head_error=error)
    monkeypatch.setattr(personalizations_module, "s3", dummy_s3)

    def _build_pdf_bytes(_job, _page_nums):
        return b"pdf-bytes"

    monkeypatch.setattr(personalizations_module, "_build_pdf_bytes", _build_pdf_bytes)

    async def _always_false(_bucket: str, _key: str, _attempts: int = 1, _delay_seconds: float = 0.0) -> bool:
        return False

    monkeypatch.setattr(personalizations_module, "_wait_for_s3_object", _always_false)

    job = make_job()
    with pytest.raises(HTTPException) as exc:
        await personalizations_module._ensure_pdf_in_s3(job, [1, 2])
    assert exc.value.status_code == 503
