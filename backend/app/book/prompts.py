from __future__ import annotations

from typing import Iterable, List, Optional


def join_prompt_parts(parts: Iterable[Optional[str]]) -> str:
    cleaned: List[str] = []
    for part in parts:
        if not part:
            continue
        value = part.strip().strip(",")
        if not value:
            continue
        cleaned.append(value)
    return ", ".join(cleaned)

