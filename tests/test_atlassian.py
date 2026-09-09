from dev_yard.atlassian import (
    collect_requirement,
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


class _FakeResp:
    def __init__(self, payload: object) -> None:
        import json as _json

        self._body = _json.dumps(payload).encode() if not isinstance(payload, bytes) else payload

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def test_http_omits_auth_header_without_credentials(monkeypatch):
    import urllib.request

    import dev_yard.atlassian as atl

    seen: list[tuple[str, dict]] = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, dict(req.headers)))
        return _FakeResp({})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    atl._http_json("https://x.example.com/api", "", "")
    atl._http_bytes("https://x.example.com/f", "", "")
    assert seen, "no requests captured"
    for _url, headers in seen:
        assert "Authorization" not in headers


def _jira_env(monkeypatch, confluence_base: str | None):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "jira-user")
    monkeypatch.setenv("JIRA_PASSWORD", "jira-secret")
    for var in (
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_URL",
        "CONFLUENCE_USERNAME",
        "CONFLUENCE_USER",
        "CONFLUENCE_PASSWORD",
        "CONFLUENCE_TOKEN",
        "CONFLUENCE_API_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    if confluence_base:
        monkeypatch.setenv("CONFLUENCE_BASE_URL", confluence_base)


def test_inferred_confluence_host_never_receives_credentials(
    tmp_path, monkeypatch
):
    import urllib.request

    import dev_yard.atlassian as atl

    _jira_env(monkeypatch, confluence_base=None)
    requests: list[tuple[str, dict]] = []

    def fake_urlopen(req, timeout=None):
        requests.append((req.full_url, dict(req.headers)))
        url = req.full_url
        if "/rest/api/2/issue/" in url:
            if "remotelink" in url:
                return _FakeResp([])
            return _FakeResp(
                {
                    "fields": {
                        "summary": "S",
                        "description": "see https://evil.example.com/pages/viewpage.action?pageId=42",
                    },
                }
            )
        if "evil.example.com" in url:
            return _FakeResp(
                {
                    "title": "Page",
                    "body": {"storage": {"value": "<p>hello</p>"}},
                    "children": {},
                }
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = atl.collect_requirement(tmp_path, "AB-1", root=tmp_path)
    evil = [h for u, h in requests if "evil.example.com" in u]
    assert evil, "inferred host was not fetched"
    for headers in evil:
        assert "Authorization" not in headers
    assert any("without credentials" in w for w in result.warnings)


def test_configured_confluence_host_still_gets_credentials(tmp_path, monkeypatch):
    import urllib.request

    import dev_yard.atlassian as atl

    _jira_env(monkeypatch, confluence_base="https://conf.example.com")
    requests: list[tuple[str, dict]] = []

    def fake_urlopen(req, timeout=None):
        requests.append((req.full_url, dict(req.headers)))
        url = req.full_url
        if "/rest/api/2/issue/" in url:
            if "remotelink" in url:
                return _FakeResp([])
            return _FakeResp(
                {
                    "fields": {
                        "summary": "S",
                        "description": "see https://conf.example.com/pages/viewpage.action?pageId=7",
                    },
                }
            )
        if "conf.example.com" in url:
            return _FakeResp(
                {
                    "title": "Page",
                    "body": {"storage": {"value": "<p>hi</p>"}},
                    "children": {},
                }
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    atl.collect_requirement(tmp_path, "AB-2", root=tmp_path)
    conf = [h for u, h in requests if "conf.example.com" in u]
    assert conf
    for headers in conf:
        assert "Authorization" in headers
