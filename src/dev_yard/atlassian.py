from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dev_yard.env import load_env
from dev_yard.jira import adf_text, jira_creds

CONFLUENCE_URL = re.compile(
    r"https?://[^\s\"'<>]+confluence[^\s\"'<>]*|"
    r"https?://[^\s\"'<>]+/pages/viewpage\.action\?pageId=\d+|"
    r"https?://[^\s\"'<>]+/display/[^\s\"'<>]+",
    re.I,
)
PAGE_ID = re.compile(r"[?&]pageId=(\d+)")
DISPLAY_PATH = re.compile(r"/display/([^/]+)/([^/?#]+)", re.I)
RI_PAGE = re.compile(r'<ri:page[^>]*ri:content-title="([^"]+)"')
RI_FILE = re.compile(r'ri:filename="([^"]+)"')
MAX_PAGES = 8
MAX_DEPTH = 2


@dataclass
class FetchResult:
    title: str
    markdown: str
    warnings: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


def _auth_header(user: str, secret: str) -> str:
    import base64

    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def _http_json(url: str, user: str, secret: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Authorization": _auth_header(user, secret), "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _http_bytes(url: str, user: str, secret: str) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": _auth_header(user, secret)})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _walk_strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_walk_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_walk_strings(v))
    return out


def extract_confluence_urls(text: str) -> list[str]:
    return list(dict.fromkeys(CONFLUENCE_URL.findall(text)))


def page_id_from_url(url: str) -> str | None:
    m = PAGE_ID.search(url)
    return m.group(1) if m else None


def space_title_from_url(url: str) -> tuple[str, str] | None:
    m = DISPLAY_PATH.search(url)
    if not m:
        return None
    space = urllib.parse.unquote(m.group(1))
    title = urllib.parse.unquote(m.group(2).replace("+", " "))
    return space, title


class _HtmlToMd(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "p":
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "tr":
            self.parts.append("\n")
        elif tag == "td" or tag == "th":
            self.parts.append(" | ")
        elif tag == "img":
            src = ad.get("src") or ""
            alt = ad.get("alt") or ad.get("data-linked-resource-default-alias") or "image"
            self.parts.append(f"![{alt}]({src})")
        elif tag == "a":
            self._href = ad.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if tag == "a":
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._href:
            self.parts.append(f"[{text}]({self._href})")
        else:
            self.parts.append(text)


def html_to_markdown(html: str) -> str:
    p = _HtmlToMd()
    p.feed(html)
    raw = "".join(p.parts)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _confluence_creds(jira_base: str, jira_user: str, jira_secret: str) -> tuple[str, str, str]:
    cbase = os.environ.get("CONFLUENCE_BASE_URL") or os.environ.get("CONFLUENCE_URL") or ""
    user = os.environ.get("CONFLUENCE_USERNAME") or os.environ.get("CONFLUENCE_USER") or jira_user
    secret = (
        os.environ.get("CONFLUENCE_PASSWORD")
        or os.environ.get("CONFLUENCE_TOKEN")
        or os.environ.get("CONFLUENCE_API_TOKEN")
        or jira_secret
    )
    return cbase.rstrip("/"), user, secret


def _infer_cbase(urls: list[str], fallback: str) -> str:
    for u in urls:
        p = urllib.parse.urlparse(u)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    return fallback


def _resolve_title(cbase: str, user: str, secret: str, space: str, title: str) -> str | None:
    q = urllib.parse.urlencode({"spaceKey": space, "title": title, "expand": "version"})
    try:
        data = _http_json(f"{cbase}/rest/api/content?{q}", user, secret)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    results = data.get("results") or []
    if not results:
        return None
    return str(results[0].get("id"))


def collect_requirement(dest: Path, key: str, root: Path | None = None) -> FetchResult:
    load_env(root)
    creds = jira_creds()
    if not creds:
        return FetchResult(
            title=key,
            markdown=f"# {key}\n",
            warnings=[
                "Jira credentials missing: need JIRA_BASE_URL plus "
                "JIRA_USERNAME/JIRA_EMAIL and JIRA_PASSWORD/JIRA_API_TOKEN"
            ],
        )
    jbase, juser, jsecret = creds
    warnings: list[str] = []
    try:
        issue = _http_json(
            f"{jbase}/rest/api/2/issue/{key}?expand=renderedFields,names",
            juser,
            jsecret,
        )
        remotes = _http_json(f"{jbase}/rest/api/2/issue/{key}/remotelink", juser, jsecret)
    except urllib.error.HTTPError as e:
        return FetchResult(key, f"# {key}\n", [f"Jira HTTP {e.code} for {key}"])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return FetchResult(key, f"# {key}\n", [f"Jira request failed: {e}"])

    fields = issue.get("fields") or {}
    title = str(fields.get("summary") or key)
    desc = fields.get("description") or ""
    if isinstance(desc, dict):
        desc = adf_text(desc)
    rendered = ((issue.get("renderedFields") or {}).get("description")) or ""

    blob = "\n".join(_walk_strings(fields) + _walk_strings(remotes))
    cf_urls = extract_confluence_urls(blob)
    cbase, cuser, csecret = _confluence_creds(jbase, juser, jsecret)
    if not cbase:
        cbase = _infer_cbase(cf_urls, "")
    if not cbase and cf_urls:
        warnings.append("Confluence URLs found but CONFLUENCE_BASE_URL unset and could not infer host")

    seed_ids: list[str] = []
    for u in cf_urls:
        pid = page_id_from_url(u)
        if pid:
            seed_ids.append(pid)
            continue
        st = space_title_from_url(u)
        if st and cbase:
            nid = _resolve_title(cbase, cuser, csecret, st[0], st[1])
            if nid:
                seed_ids.append(nid)

    assets = dest / "assets"
    pages_md: list[str] = []
    images: list[str] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(pid, 0) for pid in dict.fromkeys(seed_ids)]

    while queue and len(seen) < MAX_PAGES:
        pid, depth = queue.pop(0)
        if pid in seen or depth > MAX_DEPTH:
            continue
        if not cbase:
            break
        seen.add(pid)
        try:
            page = _http_json(
                f"{cbase}/rest/api/content/{pid}"
                f"?expand=body.storage,body.view,space,children.attachment",
                cuser,
                csecret,
            )
        except urllib.error.HTTPError as e:
            warnings.append(f"Confluence HTTP {e.code} for page {pid}")
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            warnings.append(f"Confluence fetch failed for {pid}: {e}")
            continue

        ptitle = str(page.get("title") or pid)
        space = str((page.get("space") or {}).get("key") or "")
        storage = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
        view = ((page.get("body") or {}).get("view") or {}).get("value") or ""
        html = view or storage
        page_dir = assets / pid
        page_dir.mkdir(parents=True, exist_ok=True)

        atts = (((page.get("children") or {}).get("attachment") or {}).get("results")) or []
        file_map: dict[str, str] = {}
        for att in atts:
            fname = str(att.get("title") or att.get("id"))
            aid = str(att.get("id") or "")
            mime = str(((att.get("metadata") or {}).get("mediaType")) or "")
            is_img = mime.startswith("image/") or fname.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
            )
            if not is_img:
                continue
            dl = f"{cbase}/download/attachments/{pid}/{urllib.parse.quote(fname)}"
            try:
                data = _http_bytes(dl, cuser, csecret)
            except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError):
                if aid:
                    try:
                        data = _http_bytes(
                            f"{cbase}/rest/api/content/{pid}/child/attachment/{aid}/download",
                            cuser,
                            csecret,
                        )
                    except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError) as e:
                        warnings.append(f"image download failed {fname}: {e}")
                        continue
                else:
                    warnings.append(f"image download failed {fname}")
                    continue
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", fname)
            dest_file = page_dir / safe
            dest_file.write_bytes(data)
            rel = dest_file.relative_to(dest).as_posix()
            file_map[fname] = rel
            images.append(rel)

        md = html_to_markdown(html)
        for fname, rel in file_map.items():
            md = md.replace(fname, rel)
            # rewrite remaining /download/attachments/<id>/fname
            md = re.sub(
                rf"\(/download/attachments/{pid}/{re.escape(urllib.parse.quote(fname))}[^)]*\)",
                f"({rel})",
                md,
            )
            md = re.sub(
                rf"\([^)]*{re.escape(fname)}[^)]*\)",
                f"({rel})",
                md,
            )

        url = f"{cbase}/pages/viewpage.action?pageId={pid}"
        pages_md.append(f"## Confluence: {ptitle}\n\nSource: {url}\n\n{md}\n")

        if depth < MAX_DEPTH:
            for nested in extract_confluence_urls(storage + view):
                nid = page_id_from_url(nested)
                if not nid:
                    st = space_title_from_url(nested)
                    if st:
                        nid = _resolve_title(cbase, cuser, csecret, st[0], st[1])
                if nid and nid not in seen:
                    queue.append((nid, depth + 1))
            if space:
                for title_ref in RI_PAGE.findall(storage):
                    nid = _resolve_title(cbase, cuser, csecret, space, title_ref)
                    if nid and nid not in seen:
                        queue.append((nid, depth + 1))

    jira_body = html_to_markdown(rendered) if rendered else str(desc)
    parts = [f"# {key}\n\n{title}\n\n## Jira\n\n{jira_body}\n"]
    if cf_urls:
        parts.append("## Linked Confluence\n\n" + "\n".join(f"- {u}" for u in cf_urls) + "\n")
    parts.extend(pages_md)
    md_out = "\n".join(parts).strip() + "\n"
    return FetchResult(
        title=title,
        markdown=md_out,
        warnings=warnings,
        pages=list(seen),
        images=images,
    )
