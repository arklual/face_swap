import pytest

from app.models import Job
from app.routes import personalizations as personalizations_module


def make_job(*, analysis_json):
    return Job(
        job_id="job-1",
        user_id="user-1",
        slug="book-1",
        status="analyzing_completed",
        child_name="",
        child_age=1,
        analysis_json=analysis_json,
        avatar_url="https://example.com/original.jpg",
    )


def test_job_to_personalization_includes_avatar_crop_url(monkeypatch):
    monkeypatch.setattr(personalizations_module, "_presigned_get", lambda uri, expires=3600: f"url:{uri}")

    job = make_job(analysis_json={"face_crop_uri": "s3://bucket/avatars/job-1_crop.png"})
    dto = personalizations_module._job_to_personalization(job, preview=None)

    assert dto.avatarUrl == "https://example.com/original.jpg"
    assert dto.avatarCropUrl == "url:s3://bucket/avatars/job-1_crop.png"


def test_job_to_personalization_avatar_crop_url_missing_is_none(monkeypatch):
    monkeypatch.setattr(personalizations_module, "_presigned_get", lambda uri, expires=3600: f"url:{uri}")

    job = make_job(analysis_json={})
    dto = personalizations_module._job_to_personalization(job, preview=None)

    assert dto.avatarCropUrl is None

