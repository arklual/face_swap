from __future__ import annotations

import base64
import html
import io
import mimetypes
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import boto3
from PIL import Image
from playwright.async_api import async_playwright

from ..config import settings
from ..logger import logger
from ..book.manifest import TextLayer


_s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)


DEFAULT_TEXT_SETTINGS: Dict[str, Any] = {
    # page size is injected per-call
    "target_size": 2551,
    "font_size": 70,
    "font_family": "CustomFont, 'Comic Sans MS', sans-serif",
    "font_weight": 600,
    "line_height": 1.15,
    "text_align": "left",
    "stroke_width": 0,
    "stroke_color": "#ffffff",
    "color": "#ffffff",
    "shadow_color": "0,0,0",
    "shadow_opacity": 1.0,
    "shadow_offset": 4,
    "shadow_blur": [0, 20, 40, 60],
    "box_w": 1611,
    "box_h": 1784,
    "top": 451,
    "margin_left": -36,
    "white_space": "pre-line",
}


def _merge_settings(defaults: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(defaults)
    if not override:
        return s
    s.update(override)
    return s


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _build_text_shadow_layers(
    shadow_offset: int,
    shadow_blur: List[int],
    shadow_color: str,
    shadow_opacity: float,
) -> List[str]:
    color_with_alpha = f"rgba({shadow_color},{shadow_opacity})"
    return [f"{shadow_offset}px {shadow_offset}px {blur}px {color_with_alpha}" for blur in shadow_blur]


def _build_stroke_shadow_layers(stroke_width: int, stroke_color: str) -> List[str]:
    """
    Robust outline via text-shadow (works even when -webkit-text-stroke doesn't render).
    Mirrors html_render.py behaviour.
    """
    if stroke_width <= 0:
        return []
    r, g, b = _hex_to_rgb(stroke_color)
    c = f"rgb({r},{g},{b})"
    w = stroke_width
    offsets = [
        (-w, 0), (w, 0), (0, -w), (0, w),
        (-w, -w), (-w, w), (w, -w), (w, w),
        (-w, -w // 2), (-w, w // 2), (w, -w // 2), (w, w // 2),
        (-w // 2, -w), (w // 2, -w), (-w // 2, w), (w // 2, w),
    ]
    return [f"{dx}px {dy}px 0 {c}" for dx, dy in offsets if dx or dy]


def _build_text_shadow_css(
    *,
    stroke_width: int,
    stroke_color: str,
    shadow_offset: int,
    shadow_blur: List[int],
    shadow_color: str,
    shadow_opacity: float,
) -> str:
    layers: List[str] = []
    layers.extend(_build_stroke_shadow_layers(stroke_width, stroke_color))
    layers.extend(_build_text_shadow_layers(shadow_offset, shadow_blur, shadow_color, shadow_opacity))
    return ",\n  ".join(layers) if layers else "none"


_ALLOWED_TITLE_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ALLOWED_TITLE_HTML_SPAN_OPEN_RE = re.compile(
    r"""^<\s*span\s+class\s*=\s*(?P<q>['"])\s*(?P<cls>title-big|title-small)\s*(?P=q)\s*>\s*$""",
    flags=re.IGNORECASE,
)
_ALLOWED_TITLE_HTML_SPAN_CLOSE_RE = re.compile(r"^<\s*/\s*span\s*>\s*$", flags=re.IGNORECASE)
_ALLOWED_TITLE_HTML_BR_RE = re.compile(r"^<\s*br\s*/?\s*>\s*$", flags=re.IGNORECASE)


def _sanitize_title_html(text_html: str) -> str:
    """
    Whitelist-only HTML sanitizer:
    - allows <span class="title-big|title-small">, </span>, <br/> (case-insensitive)
    - escapes everything else (including attributes, other tags)
    Also escapes all plain-text segments.
    """
    out: List[str] = []
    last_end = 0
    for m in _ALLOWED_TITLE_HTML_TAG_RE.finditer(text_html):
        start, end = m.span()
        if start > last_end:
            out.append(html.escape(text_html[last_end:start]))
        tag = text_html[start:end]
        if _ALLOWED_TITLE_HTML_SPAN_OPEN_RE.match(tag) or _ALLOWED_TITLE_HTML_SPAN_CLOSE_RE.match(tag) or _ALLOWED_TITLE_HTML_BR_RE.match(tag):
            out.append(tag)
        else:
            out.append(html.escape(tag))
        last_end = end
    if last_end < len(text_html):
        out.append(html.escape(text_html[last_end:]))
    return "".join(out)


def _bytes_to_data_uri(data: bytes, mime: str) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _pil_to_png_data_uri(img: Image.Image, target_size: int) -> str:
    if img.size != (target_size, target_size):
        img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return _bytes_to_data_uri(buf.getvalue(), "image/png")


def _s3_read_bytes(uri_or_key: str) -> bytes:
    """
    Read bytes from S3 for:
    - s3://bucket/key
    - relative key (uses configured bucket)
    """
    bucket = settings.S3_BUCKET_NAME
    key = uri_or_key
    if uri_or_key.startswith("s3://"):
        p = urlparse(uri_or_key)
        bucket = p.netloc or bucket
        key = p.path.lstrip("/")

    obj = _s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def _font_to_data_uri(font_uri: str) -> str:
    mime, _ = mimetypes.guess_type(font_uri)
    if not mime:
        mime = "application/octet-stream"
    data = _s3_read_bytes(font_uri)
    return _bytes_to_data_uri(data, mime)


def _render_template(layer: TextLayer, template_vars: Dict[str, Any]) -> str:
    """
    Render layer text using the configured template engine.
    Currently supported:
    - format: Python str.format_map
    """
    if layer.text_template:
        template = layer.text_template
    elif layer.text_key:
        # Minimal fallback: treat text_key as literal text if no external store is wired yet.
        template = layer.text_key
    else:
        raise ValueError("TextLayer has neither text_template nor text_key")

    engine = (layer.template_engine or "format").lower().strip()
    if engine != "format":
        raise ValueError(f"Unsupported template_engine: {layer.template_engine}")

    # format_map is safe-ish for plain string templates; later we can switch to Jinja2 sandbox.
    try:
        rendered = template.format_map(template_vars)
    except Exception as e:
        raise ValueError(f"Template render failed: {e}")

    # Important: treat it as plain text only; escape before embedding into HTML.
    return rendered


def _build_html(
    bg_data_uri: str,
    font_data_uri: str,
    text_plain_or_html: str,
    settings_dict: Dict[str, Any],
    *,
    allow_title_html: bool,
) -> str:
    target_size = settings_dict["target_size"]
    stroke_width = int(settings_dict.get("stroke_width", 0) or 0)
    stroke_color = str(settings_dict.get("stroke_color", "#ffffff") or "#ffffff")

    title_big_size = int(
        settings_dict.get("title_big_size", max(int(settings_dict["font_size"]) * 2, int(settings_dict["font_size"]) + 80))
    )
    title_small_size = int(settings_dict.get("title_small_size", int(settings_dict["font_size"])))

    text_shadow_css = _build_text_shadow_css(
        stroke_width=stroke_width,
        stroke_color=stroke_color,
        shadow_offset=int(settings_dict["shadow_offset"]),
        shadow_blur=list(settings_dict["shadow_blur"]),
        shadow_color=str(settings_dict["shadow_color"]),
        shadow_opacity=float(settings_dict["shadow_opacity"]),
    )

    if allow_title_html:
        safe_text = _sanitize_title_html(text_plain_or_html)
    else:
        # Escape to prevent HTML/JS injection; keep newlines as-is (white-space: pre-line).
        safe_text = html.escape(text_plain_or_html)

    font_face = ""
    if font_data_uri:
        font_face = f"""
@font-face {{
  font-family: 'CustomFont';
  src: url('{font_data_uri}');
}}
""".strip()

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{font_face}

html, body {{
  margin: 0;
  padding: 0;
  width: {target_size}px;
  height: {target_size}px;
  overflow: hidden;
}}

body {{
  background: url('{bg_data_uri}') center center / cover no-repeat;
  display: flex;
  justify-content: center;
  align-items: flex-start;
}}

.text {{
  position: relative;
  margin-top: {settings_dict['top']}px;
  margin-left: {settings_dict['margin_left']}px;
  width: {settings_dict['box_w']}px;
  height: {settings_dict['box_h']}px;
}}

.fill {{
  color: {settings_dict['color']};
  font-family: {settings_dict['font_family']};
  font-size: {settings_dict['font_size']}px;
  font-weight: {settings_dict['font_weight']};
  line-height: {settings_dict['line_height']};
  text-align: {settings_dict['text_align']};
  white-space: {settings_dict['white_space']};

  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;

  /* Stroke (outline) */
  text-stroke: {stroke_width}px {stroke_color};
  -webkit-text-stroke: {stroke_width}px {stroke_color};
  paint-order: stroke fill;

  text-shadow:
  {text_shadow_css};
}}

.fill * {{
  /* Ensure stroke applies to nested spans (text-stroke is not inherited) */
  -webkit-text-stroke: inherit;
  text-stroke: inherit;
  paint-order: inherit;
}}

.title-big {{
  font-size: {title_big_size}px;
  line-height: 1.0;
  display: inline-block;
}}

.title-small {{
  font-size: {title_small_size}px;
  line-height: 1.05;
  display: inline-block;
}}
</style>
</head>

<body>
  <div class="text">
    <div class="fill">{safe_text}</div>
  </div>
</body>
</html>
"""


async def render_text_layers_over_image(
    bg_img: Image.Image,
    layers: List[TextLayer],
    *,
    template_vars: Dict[str, Any],
    output_px: int,
) -> Image.Image:
    """
    Render one or multiple TextLayer overlays over a background image, using Playwright.

    Returns a new PIL.Image (RGB).
    """
    if not layers:
        return bg_img

    # Cache font data URIs by font_uri to avoid repeated S3 reads.
    font_cache: Dict[str, str] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        cur = bg_img
        for layer in layers:
            text_plain_or_html = _render_template(layer, template_vars)

            style = _merge_settings(DEFAULT_TEXT_SETTINGS, layer.style or {})
            style["target_size"] = int(output_px)
            allow_title_html = bool(style.get("allow_title_html"))

            bg_data_uri = _pil_to_png_data_uri(cur, int(output_px))

            font_data_uri = ""
            font_uri = None
            # Allow font_uri either as explicit field (future) or embedded into style dict.
            if isinstance(getattr(layer, "font_uri", None), str) and getattr(layer, "font_uri"):
                font_uri = getattr(layer, "font_uri")
            elif isinstance(layer.style, dict) and isinstance(layer.style.get("font_uri"), str):
                font_uri = layer.style.get("font_uri")

            if font_uri:
                if font_uri not in font_cache:
                    font_cache[font_uri] = _font_to_data_uri(font_uri)
                font_data_uri = font_cache[font_uri]

            html_doc = _build_html(
                bg_data_uri=bg_data_uri,
                font_data_uri=font_data_uri,
                text_plain_or_html=text_plain_or_html,
                settings_dict=style,
                allow_title_html=allow_title_html,
            )

            page = await browser.new_page(viewport={"width": int(output_px), "height": int(output_px)})
            try:
                # Block any accidental external requests.
                async def _route(route, request):
                    url = request.url or ""
                    if url.startswith("data:") or url.startswith("about:"):
                        return await route.continue_()
                    return await route.abort()

                await page.route("**/*", _route)
                await page.set_content(html_doc, wait_until="networkidle")
                png_bytes = await page.screenshot(type="png")
            finally:
                await page.close()

            cur = Image.open(io.BytesIO(png_bytes)).convert("RGB")

        await browser.close()

    logger.debug("Rendered text layers via Playwright", extra={"layers": len(layers), "output_px": output_px})
    return cur


