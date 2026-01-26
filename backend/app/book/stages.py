from __future__ import annotations

from typing import List, Literal

from .manifest import BookManifest

Stage = Literal["prepay", "postpay"]

FRONT_HIDDEN_PAGE_NUMS = {1, 23}


def _exclude_front_hidden_pages(page_nums: List[int]) -> List[int]:
    return [page_num for page_num in page_nums if page_num not in FRONT_HIDDEN_PAGE_NUMS]


def front_visible_page_nums(manifest: BookManifest) -> List[int]:
    nums = sorted({p.page_num for p in manifest.pages})
    return _exclude_front_hidden_pages(nums)


def postpay_page_nums(manifest: BookManifest) -> List[int]:
    nums: List[int] = []
    seen = set()
    for page in manifest.pages:
        if not page.availability.postpay:
            continue
        if page.page_num in seen:
            continue
        seen.add(page.page_num)
        nums.append(page.page_num)
    return nums


def prepay_page_nums(manifest: BookManifest) -> List[int]:
    """
    Prepay generates the first spread + next page (3 pages) from front-visible postpay pages.
    Hidden pages (1, 23) are excluded.
    """
    postpay_nums = postpay_page_nums(manifest)
    visible_nums = [n for n in postpay_nums if n not in FRONT_HIDDEN_PAGE_NUMS]
    return visible_nums[:3]


def page_nums_for_stage(manifest: BookManifest, stage: Stage) -> List[int]:
    """
    Resolve the list of page numbers for a stage.

    Product requirement:
    - prepay: first spread + next page (3 pages) from front-visible postpay pages
    - postpay: everything that is allowed for postpay
    """
    if stage == "prepay":
        return prepay_page_nums(manifest)

    return postpay_page_nums(manifest)


def page_nums_for_front_preview(manifest: BookManifest, stage: Stage) -> List[int]:
    """
    Front-facing preview excludes hidden pages (e.g. 1 and 23).
    """
    return _exclude_front_hidden_pages(page_nums_for_stage(manifest, stage))


def stage_has_face_swap(manifest: BookManifest, stage: Stage) -> bool:
    """
    Return True if the given stage contains at least one page that requires face swap.

    Used to skip GPU/Comfy stage entirely for text-only / no-op stages.
    """
    page_nums = page_nums_for_stage(manifest, stage)
    for page_num in page_nums:
        spec = manifest.page_by_num(page_num)
        if spec and spec.needs_face_swap:
            return True
    return False

