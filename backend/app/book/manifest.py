from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class Availability(BaseModel):
    """
    Page availability by stage.
    - prepay: generated/returned before payment
    - postpay: generated/returned after payment
    """

    prepay: bool = False
    postpay: bool = True


class TextLayer(BaseModel):
    """
    One text overlay pass (rendered with html_render-style pipeline).

    NOTE: We keep this minimal on purpose; we can extend with multiple fonts,
    alignment presets, etc.
    """

    text_key: Optional[str] = None
    text_template: Optional[str] = None

    # Template system (we start with python .format_map)
    template_engine: str = "format"
    template_vars: List[str] = Field(default_factory=lambda: ["child_name"])

    # Optional font file stored in S3 (TTF/OTF). If missing - system fonts/fallback used.
    font_uri: Optional[str] = None

    # CSS-like rendering settings (compatible with html_render.py)
    style: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _validate_text_source(self) -> 'TextLayer':
        if not self.text_key and not self.text_template:
            raise ValueError("TextLayer requires either text_key or text_template")
        return self


class PageSpec(BaseModel):
    page_num: int
    base_uri: str

    needs_face_swap: bool = False
    text_layers: List[TextLayer] = Field(default_factory=list)

    availability: Availability = Field(default_factory=Availability)

    # Optional prompt controls for ComfyUI workflow (fallbacks are allowed)
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None


class OutputSpec(BaseModel):
    dpi: int = 300
    page_size_px: int = 2551


class BookManifest(BaseModel):
    slug: str
    pages: List[PageSpec]
    output: OutputSpec = Field(default_factory=OutputSpec)

    def page_by_num(self, page_num: int) -> Optional[PageSpec]:
        for p in self.pages:
            if p.page_num == page_num:
                return p
        return None


