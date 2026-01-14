"""
HTML/CSS renderer for pages, saved as PNG.

Requires:
  python3 -m pip install playwright pillow
  python3 -m playwright install chromium

Usage:
  python3 html_render.py
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from copy import deepcopy

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).parent

# Mapping: array index -> page number
# Added 0 at start, shifted previous numbers by +1
PAGE_NUMBERS = [0, 1, 2, 4, 6, 8, 9, 10, 13, 14, 15, 16, 19, 20, 22]

PAGE = [
    # New Page 0
    ('<span class="title-big">АЛИНА</span><br/>'
     '<span class="title-small">И ФОНАРИК ДОБРОТЫ</span>'),

    # Old Page 0 -> Page 1
    ("Сказка “Алина и фонарик доброты” о том, что добро — это не великие подвиги, "
    "а маленькие поступки, которые делают жизнь вокруг чуть лучше.\n"
    "Она учит замечать чужие трудности и не проходить мимо, даже если помощь "
    "кажется пустяковой.\n"
    "Показывает, что забота о других делает человека светлее и счастливее изнутри.\n"
    "У каждого есть свой «фонарик доброты», и он загорается, когда мы выбираем "
    "быть внимательными и добрыми к людям рядом."),
    
    # Old Page 1 -> Page 2
    ("Вечером, как всегда, Алина лежала в своей постели, а мама читала ей сказку перед сном. "
    "Про смелых героев, которые совершают добрые поступки и делают мир лучше. "
    "«Добро всегда побеждает.» - говорилось в книжке.\n"
    "Засыпая, Алина задумалась: а как это — делать добро?"),

    # Old Page 2 -> Page 3 (skipped in mapping logic previously, kept consistent with shift)
    ("Утром Алина за завтраком спросила маму:\n"
    "— Мам, расскажи, что значит делать добро?\n"
    "— Это значит сделать что-то полезное и хорошее, помочь кому-то. Добро — как фонарик:"
    "где оно есть, там светлее — мягко ответила мама. — Смотри вокруг внимательно, и ты всё поймёшь."),
    
    ("Алина вышла во двор. Солнце грело качели, по лужам прыгали воробьи. Девочка шла по "
    "дорожке \nи оглядывалась по сторонам: ни драконов,\nни рыцарей, ни волшебников — "
    "только обычный двор.\n\n"
    "У детской площадки стоял Гоша и грустно смотрел\nна свой шлем, он никак не мог "
    "застегнуть его."),
    
    ("— Давай я подержу, а ты проденешь, — предложила Алина.\n"
    "Они щёлкнули застёжку вместе."),
    
    ("— Спасибо! — крикнул Гоша, подал девочке «пять»\n и помчался по дорожке,\nкак ветер."),
    
    ("Алина пошла дальше и заметила на дорожке толстую палку — как раз там, где ездят "
    "велосипедисты.\nДевочка представила себя на велосипеде и подумала, что ей бы очень не "
    "хотелось наехать на такое препятствие, ведь его можно не заметить и упасть с "
    "велосипеда, а это бывает крайне неприятно.\n\n"
    "Алина стащила палку в сторону и положила у клумбы, чтобы никто не споткнулся."),
    
    ("Возле песочницы сидела малышка Лида и едва не плакала.\n"
    "— Что случилось? Тебя кто-то обидел? — спросила Алина.\n"
    "— Я потеряла свою любимую розовую лопатку, — грустно ответила Лида.\n"
    "— Давай искать вместе, — сказала Алина."),
    
    ("Они внимательно осматривали песочницу, переворачивали все формочки, и вдруг Алина "
    "заметила, что под лежащим у края песочницы ведёрком торчала розовая ручка. "),
    
    ("Алина "
    "протянула ведёрко, а малышка Лида сразу обрадовалась, увидев любимую лопатку, и "
    "прижала её к себе:\n"
    "— Большое спасибо!"),
    
    ("У подъезда молодая мама пыталась протиснуть коляску с малышом через входную дверь, "
    "которая так и норовила закрыться и задеть коляску."),
    
    ("— Я подержу, — сказала Алина и прижала дверь плечом. Мама малыша сказала:\n"
    "— Какая ты молодец!"),
    
    ("Алина ещё прошлась по двору. Она не встретила никаких драконов, но на душе было "
    "светло-светло, будто её фонарик светил сам по себе."),
    
    ("Вечером мама спросила:\n"
    "— Ну как твой фонарик? Удалось совершить доброе дело?\n"
    "— Нет — задумалась Алина. — Никаких особенных добрых дел не получилось, я просто "
    "помогла Гоше застегнуть шлем, перетащила палку подальше от велосипедной дорожки, "
    "нашла Лидину лопатку, помогла маме с коляской войти в подъезд, Ничаких подвигов.\n"
    "Мама прижала дочку к плечу:\n"
    "— Именно это и есть подвиги. Ты помогла людям рядом, а твой фонарик доброты сегодня "
    "светил целый день.\n"
    "— Правда? — улыбнулась Алина. — Значит, завтра я снова буду смотреть внимательным "
    "глазами.")

]


def file_to_data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        mime = "application/octet-stream"
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


# Default rendering settings (centralized configuration block)
DEFAULT_TEXT_SETTINGS = {
    # canvas size / output
    "target_size": 2551,

    # text appearance
    "font_size": 70,
    "font_family": "CustomFont, 'Comic Sans MS', sans-serif",
    "font_weight": 600,
    "line_height": 1.15,
    "text_align": "left",  # left, right, center
    "stroke_width": 0,  # px
    "stroke_color": "#ffffff",

    # text color & shadow
    "color": "#ffffff",
    "shadow_color": "0,0,0",  # RGB without alpha
    "shadow_opacity": 1.0,
    "shadow_offset": 4,
    # shadow_blur is a list of blur radii that will be applied as multiple shadow layers
    "shadow_blur": [0, 20, 40, 60],

    # box & placement (defaults match previous values)
    "box_w": 1611,
    "box_h": 1784,
    "top": 451,
    "margin_left": -36,

    # Misc
    "white_space": "pre-line",
}

# Per-page overrides. Keys are page numbers (as in PAGE_NUMBERS).
# Example: override color and position for page 3:
# PAGE_STYLE_OVERRIDES = {3: {"color": "#000000", "top": 300}}
# Per-page overrides. All previous keys shifted by +1.
PAGE_STYLE_OVERRIDES: dict[int, dict] = {
  # New Page 0 (Title)
  0: {
    "text_align": "center",
    "box_w": 1831,
    "top": 300, # Approximate center, adjust if needed
    "font_path": str(ROOT / "ofont.ru_To Japan.ttf"),
    "font_family": "CustomFont, 'ofont.ru_To Japan', serif",
    "font_size": 110,
    "title_big_size": 180,
    "font_weight": 500,
    "color": "#ab5792",
    "stroke_width": 5,
    "stroke_color": "#ffffff",
    "shadow_color": "0,0,0",
    "shadow_opacity": 0.75,
    "shadow_offset": 4,
    "shadow_blur": [0, 8, 16, 24, 32, 40],
    "title_small_size": 100
  },
  # Old 0 -> 1
  1: {
    "text_align": "center"
  },
  # Old 1 -> 2
  2: {
    "box_w": 1831,
    "box_h": 1784,
    "top": 691,
    "margin_left": -36,
  },
  # Old 3 -> 4
  4: {
    "box_w": 1891,
    "box_h": 1784,
    "top": 781,
    "margin_left": -36,
  },
  # Old 5 -> 6
  6: {
    "box_w": 1851,
    "box_h": 1784,
    "top": 811,
    "margin_left": -36,
  },
  # Old 7 -> 8
  8: {
    "box_w": 950,
    "box_h": 1784,
    "top": 1591,
    "margin_left": 1036,
  },
  # Old 8 -> 9
  9: {
    "box_w": 1050,
    "box_h": 1784,
    "top": 751,
    "margin_left": -1036,
  },
  # Old 9 -> 10
  10: {
    "box_w": 1931,
    "box_h": 1784,
    "top": 701,
    "margin_left": -36,
  },
  # Old 12 -> 13
  13: {
    "box_w": 1831,
    "box_h": 1784,
    "top": 621,
    "margin_left": -36,
  },
  # Old 13 -> 14
  14: {
    "box_w": 1611,
    "box_h": 1784,
    "top": 401,
    "margin_left": -36,
  },
  # Old 14 -> 15
  15: {
    "box_w": 1831,
    "box_h": 1784,
    "top": 1451,
    "margin_left": -36,
  },
  # Old 15 -> 16
  16: {
    "box_w": 1831,
    "box_h": 1784,
    "top": 951,
    "margin_left": -36,
  },
  # Old 18 -> 19
  19: {
    "box_w": 1831,
    "box_h": 1784,
    "top": 1151,
    "margin_left": -36,
    "color": "#95702f",
    "shadow_opacity": 0.0
  },
  # Old 19 -> 20
  20: {
    "box_w": 1831,
    "box_h": 1784,
    "top": 821,
    "margin_left": -36,
  },
  # Old 21 -> 22
  22: {
    "box_w": 1931,
    "box_h": 1684,
    "top": 651,
    "margin_left": -36,
  },
}


def merge_settings(defaults: dict, override: dict) -> dict:
    s = deepcopy(defaults)
    if not override:
        return s
    for k, v in override.items():
        s[k] = v
    return s


def upscale_image_to_data_uri(path: Path, target_size: int = DEFAULT_TEXT_SETTINGS["target_size"]) -> str:
    """Load image, upscale to target_size x target_size, return as data URI."""
    img = Image.open(path)
    img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    
    # Convert to bytes
    import io
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    data = buffer.getvalue()
    
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def build_text_shadow_layers(shadow_offset: int, shadow_blur: list[int], shadow_color: str, shadow_opacity: float) -> list[str]:
    """Construct text-shadow layers for drop shadow imitation."""
    color_with_alpha = f"rgba({shadow_color},{shadow_opacity})"
    return [f"{shadow_offset}px {shadow_offset}px {blur}px {color_with_alpha}" for blur in shadow_blur]


def build_stroke_shadow_layers(stroke_width: int, stroke_color: str) -> list[str]:
    """
    Robust outline via text-shadow (works even when -webkit-text-stroke doesn't render).
    """
    if stroke_width <= 0:
        return []
    r, g, b = hex_to_rgb(stroke_color)
    c = f"rgb({r},{g},{b})"
    w = stroke_width
    offsets = [
        (-w, 0), (w, 0), (0, -w), (0, w),
        (-w, -w), (-w, w), (w, -w), (w, w),
        (-w, -w // 2), (-w, w // 2), (w, -w // 2), (w, w // 2),
        (-w // 2, -w), (w // 2, -w), (-w // 2, w), (w // 2, w),
    ]
    # blur=0 to keep edge crisp; multiple offsets approximate round stroke
    return [f"{dx}px {dy}px 0 {c}" for dx, dy in offsets if dx or dy]


def build_html(
    bg_data_uri: str,
    font_data_uri: str,
    text: str,
    settings: dict,
) -> str:
    target_size = settings["target_size"]
    stroke_width = int(settings.get("stroke_width", 0) or 0)
    stroke_color = settings.get("stroke_color", "#ffffff")
    title_big_size = int(settings.get("title_big_size", max(settings["font_size"] * 2, settings["font_size"] + 80)))
    title_small_size = int(settings.get("title_small_size", settings["font_size"]))

    shadow_layers = []
    # Outline first, then drop shadow
    shadow_layers.extend(build_stroke_shadow_layers(stroke_width, stroke_color))
    shadow_layers.extend(
        build_text_shadow_layers(
            settings["shadow_offset"],
            settings["shadow_blur"],
            settings["shadow_color"],
            settings["shadow_opacity"],
        )
    )
    text_shadow_css = ",\n  ".join(shadow_layers) if shadow_layers else "none"

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>

@font-face {{
  font-family: 'CustomFont';
  src: url('{font_data_uri}');
}}

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
  margin-top: {settings['top']}px;
  margin-left: {settings['margin_left']}px;
  width: {settings['box_w']}px;
  height: {settings['box_h']}px;
}}

.fill {{
  color: {settings['color']};
  font-family: {settings['font_family']};
  font-size: {settings['font_size']}px;
  font-weight: {settings['font_weight']};
  line-height: {settings['line_height']};
  text-align: {settings['text_align']};
  white-space: {settings['white_space']};

  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;

  /* Stroke (outline) */
  text-stroke: {stroke_width}px {stroke_color};
  -webkit-text-stroke: {stroke_width}px {stroke_color};
  paint-order: stroke fill;

  /* Photoshop Drop Shadow imitation */
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
    <div class="fill">{text}</div>
  </div>
</body>
</html>
"""


async def render_page(page_num: int, text: str, browser) -> None:
    """Render a single page with given text."""
    page_dir = ROOT / f"page_{page_num}"
    out_path = page_dir / f"page_{page_num}_text_ready_html.png"

    # Try different background image naming conventions
    bg_path_options = [
        page_dir / f"page_{page_num}_text.jpg",
        page_dir / f"page_{page_num}_face_text.jpg",
        page_dir / f"page_{page_num}_text_face.jpg",
    ]
    
    bg_path = None
    for path in bg_path_options:
        if path.exists():
            bg_path = path
            break
    
    if bg_path is None:
        print(f"[skip] page_{page_num}: background image not found")
        return

    # Upscale background image to target size
    # Determine target size from settings (allow per-page override)
    page_settings = merge_settings(DEFAULT_TEXT_SETTINGS, PAGE_STYLE_OVERRIDES.get(page_num, {}))
    font_path = page_settings.get("font_path") or (ROOT / "Comic Sans MS.ttf")
    if isinstance(font_path, str):
        font_path = Path(font_path)
    if not font_path.exists():
        print(f"[warning] font file {font_path} not found. Falling back to system font.")
    bg_data_uri = upscale_image_to_data_uri(bg_path, page_settings["target_size"])

    html = build_html(
        bg_data_uri=bg_data_uri,
        font_data_uri=file_to_data_uri(font_path) if font_path.exists() else "",
        text=text,
        settings=page_settings,
    )

    page = await browser.new_page(
        viewport={"width": page_settings["target_size"], "height": page_settings["target_size"]},
    )
    await page.set_content(html, wait_until="networkidle")
    await page.screenshot(path=str(out_path))
    await page.close()

    print(f"[html ok] {out_path.relative_to(ROOT)}")


async def render_all_pages() -> None:
    """Render all pages in the PAGE array."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        for idx, text in enumerate(PAGE):
            if idx >= len(PAGE_NUMBERS):
                print(f"[warning] No page number mapping for index {idx}")
                continue
            
            page_num = PAGE_NUMBERS[idx]
            await render_page(page_num, text, browser)
        
        await browser.close()
    
    print(f"\n[done] Rendered {len(PAGE)} pages")


def main() -> None:
    asyncio.run(render_all_pages())


if __name__ == "__main__":
    main()
