import pytest
from pydantic import ValidationError

from app.book.manifest import BookManifest
from app.book.prompts import join_prompt_parts


def _manifest_dict(**overrides):
    base = {
        "slug": "book-slug",
        "positive_prompt": "storybook, high quality",
        "pages": [
            {
                "page_num": 1,
                "base_uri": "s3://bucket/templates/book-slug/page_01.png",
                "needs_face_swap": True,
                "availability": {"prepay": False, "postpay": True},
            }
        ],
        "output": {"dpi": 300, "page_size_px": 2551},
    }
    base.update(overrides)
    return base


def test_book_manifest_requires_positive_prompt():
    manifest = BookManifest.model_validate(_manifest_dict())
    assert manifest.positive_prompt == "storybook, high quality"

    with pytest.raises(ValidationError):
        BookManifest.model_validate(_manifest_dict(positive_prompt="   "))

    data = _manifest_dict()
    data.pop("positive_prompt")
    with pytest.raises(ValidationError):
        BookManifest.model_validate(data)


def test_join_prompt_parts_builds_comfy_positive_prompt():
    result = join_prompt_parts(["storybook style", "child portrait", None, ""])
    assert result == "storybook style, child portrait"

