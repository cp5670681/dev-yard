from __future__ import annotations

from html import escape
from html.parser import HTMLParser

_ALLOWED = frozenset(
    {
        "p",
        "br",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "pre",
        "code",
        "blockquote",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "em",
        "strong",
        "a",
        "img",
    }
)
_VOID = frozenset({"br", "hr", "img"})
_SKIP = frozenset({"script", "style", "iframe", "object", "embed", "link", "meta", "svg"})
_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title"}),
    "code": frozenset({"class"}),
    "pre": frozenset({"class"}),
    "th": frozenset({"align"}),
    "td": frozenset({"align"}),
}


def _safe_url(value: str) -> str | None:
    v = value.strip()
    low = v.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return None
    return v


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip or tag not in _ALLOWED:
            return
        extra: list[str] = []
        allowed = _ATTRS.get(tag, frozenset())
        for name, value in attrs:
            if name not in allowed or value is None:
                continue
            if name in {"href", "src"}:
                value = _safe_url(value)
                if value is None:
                    continue
            extra.append(f'{name}="{escape(value, quote=True)}"')
        attr = (" " + " ".join(extra)) if extra else ""
        self.out.append(f"<{tag}{attr}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            if self._skip:
                self._skip -= 1
            return
        if self._skip or tag not in _ALLOWED or tag in _VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip or tag in _SKIP:
            return
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and tag in _ALLOWED:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(escape(data))


def sanitize_html(src: str) -> str:
    parser = _Clean()
    parser.feed(src)
    parser.close()
    return "".join(parser.out)
