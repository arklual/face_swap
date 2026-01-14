from __future__ import annotations

from typing import List, Literal

from .manifest import BookManifest

Stage = Literal["prepay", "postpay"]

PREPAY_PAGES_COUNT = 2


def _prepay_page_nums(manifest: BookManifest) -> List[int]:
    """
    Prepay should generate the first N pages of the book (from the manifest),
    so numbering can safely start from page_00 when templates are 0-based.
    """
    # Prefer pages explicitly allowed for prepay.
    candidates = sorted({p.page_num for p in manifest.pages if p.availability.prepay})
    if not candidates:
        # Fallback: first pages by numeric order.
        candidates = sorted({p.page_num for p in manifest.pages})
    return candidates[:PREPAY_PAGES_COUNT]


def page_nums_for_stage(manifest: BookManifest, stage: Stage) -> List[int]:
    """
    Resolve the list of page numbers for a stage.

    Product requirement:
    - prepay: first N pages from the manifest (prefer `availability.prepay=True`)
    - postpay: everything else that is allowed for postpay
    """
    if stage == "prepay":
        return _prepay_page_nums(manifest)

    nums: List[int] = []
    for p in manifest.pages:
        if not p.availability.postpay:
            continue
        nums.append(p.page_num)
    return sorted(set(nums))


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

