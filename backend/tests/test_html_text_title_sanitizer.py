from app.rendering.html_text import _sanitize_title_html


def test_sanitize_title_html_allows_expected_title_markup() -> None:
    src = '<span class="title-big">АЛИНА</span><br/><span class="title-small">И ФОНАРИК ДОБРОТЫ</span>'
    assert _sanitize_title_html(src) == src


def test_sanitize_title_html_escapes_disallowed_tags_and_attributes() -> None:
    src = '<span class="title-big" onclick="alert(1)">X</span><script>alert(1)</script><br>Y'
    out = _sanitize_title_html(src)

    # Keeps the text content, but should not allow script nor extra attributes on span.
    assert "X" in out
    assert "Y" in out
    assert "<script>" not in out
    assert "onclick" not in out
