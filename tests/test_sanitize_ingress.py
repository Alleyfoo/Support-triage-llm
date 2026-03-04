from app.sanitize import sanitize_ingress_text, sanitize_text


def test_sanitize_text_strips_invisible_unicode():
    cleaned, had_invisible = sanitize_text("hello\u200b\u202eworld")
    assert had_invisible is True
    assert cleaned == "helloworld"


def test_sanitize_ingress_html_removes_hidden_content():
    html = "<div>Visible</div><span style='display:none'>Ignore me</span>"
    cleaned, flags = sanitize_ingress_text(html, is_html=True)
    assert "Visible" in cleaned
    assert "Ignore me" not in cleaned
    assert flags["had_hidden_html"] is True
