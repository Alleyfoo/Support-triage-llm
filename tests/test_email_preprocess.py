from app.email_preprocess import (
    clean_email,
    html_to_text,
    strip_signatures,
    strip_quoted_replies,
)


def test_html_to_text_simple_paragraphs():
    html = "<p>Hello <strong>World</strong></p><p>Line 2</p>"
    assert html_to_text(html) == "Hello World\nLine 2"


def test_strip_signatures_removes_trailing_block():
    text = "Hello\nThanks,\nAlice"
    assert strip_signatures(text) == "Hello"


def test_strip_quoted_replies_removes_block():
    text = "Reply line\nOn Tue, Bob wrote:\n> previous"
    assert strip_quoted_replies(text) == "Reply line"


def test_clean_email_full_flow():
    html_body = "<div>Hello team<br><br>Thanks,<br>Alice</div><div>On Tue Bob wrote:</div>"
    cleaned = clean_email(html_body, is_html=True)
    assert cleaned == "Hello team"

