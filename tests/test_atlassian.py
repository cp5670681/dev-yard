from dev_yard.atlassian import (
    extract_confluence_urls,
    html_to_markdown,
    page_id_from_url,
    space_title_from_url,
)


def test_extract_confluence_urls():
    text = (
        "see https://confluence.rccchina.com/pages/viewpage.action?pageId=669942819 "
        "and https://example.com/other"
    )
    urls = extract_confluence_urls(text)
    assert any("669942819" in u for u in urls)
    assert page_id_from_url(urls[0]) == "669942819"


def test_display_url_space_title():
    url = "https://confluence.example.com/display/SPACE/Hello+World"
    assert space_title_from_url(url) == ("SPACE", "Hello World")
    assert page_id_from_url(url) is None
    urls = extract_confluence_urls(f"see {url}")
    assert urls and "/display/SPACE/" in urls[0]


def test_html_to_markdown_images():
    html = "<h1>Hi</h1><p>x</p><img src='/download/attachments/1/a.png' alt='shot' />"
    md = html_to_markdown(html)
    assert "# Hi" in md
    assert "![shot](/download/attachments/1/a.png)" in md
