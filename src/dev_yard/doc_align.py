"""Rewrite conflicting requirement docs from a human review note.

The agent only proposes. The four markdown files change when the person
confirms the diff. Ticket state, code, and case expectations stay put.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from dev_yard import paths
from dev_yard import status as st
from dev_yard.fsutil import atomic_write_bytes
from dev_yard.qa_review import iter_case_markdown, mark_stale
from dev_yard.reqboard import save_doc
from dev_yard.runners import RunResult, get_runner
from dev_yard.service import _restore, _snapshot
from dev_yard.stages import StageSpec, resolve_doc_align_extension_path
from dev_yard.tickets import parse_tickets

DOC_FILES: tuple[str, ...] = (
    "REQUIREMENT.md",
    "GRILL.md",
    "SPEC.md",
    "TICKETS.md",
)
_SLUG = {
    "REQUIREMENT.md": "requirement",
    "GRILL.md": "grill",
    "SPEC.md": "spec",
    "TICKETS.md": "tickets",
}
# QA design reads these three. A GRILL-only sentence does not stale cases.
_STALE_ON = frozenset({"REQUIREMENT.md", "SPEC.md", "TICKETS.md"})
_MANAGED = frozenset(DOC_FILES + ("STATUS.yaml", "qa", ".doc-align"))
_DECISION_MAX = 200
_OUTSIDE = "模型改了提案以外的文件，已撤回"


DOC_ALIGN_SPEC = StageSpec(
    name="doc-align",
    skill="doc-align",
    bundles=("doc-align",),
    tools=("read", "grep", "find", "ls", "write"),
    protects=DOC_FILES + ("STATUS.yaml",),
    order=54,
    builtin=False,
    guidance=(
        "Write only the proposal JSON named in the prompt. "
        "Do not modify REQUIREMENT.md, GRILL.md, SPEC.md, TICKETS.md, "
        "STATUS.yaml, qa cases, or source code."
    ),
)


def check_request(root: Path, jira: str, ticket_id: str, summary: str) -> None:
    """Reject a propose call before a job starts."""
    if not (summary or "").strip():
        raise ValueError("审查意见不能为空")
    _require_ticket(root, jira, ticket_id)


def _require_ticket(root: Path, jira: str, ticket_id: str) -> Path:
    req = _req(root, jira)
    if ticket_id not in {t.id for t in parse_tickets(_read(req, "TICKETS.md"))}:
        raise ValueError(f"unknown ticket {ticket_id}")
    return req


def propose(
    root: Path,
    jira: str,
    ticket_id: str,
    summary: str,
    decision: str = "",
    *,
    runner: Any = None,
) -> dict[str, Any]:
    """Ask the agent for a doc proposal. Does not write the four docs."""
    check_request(root, jira, ticket_id, summary)
    if runner is None and resolve_doc_align_extension_path(root) is None:
        raise RuntimeError("doc-align 写保护扩展缺失")
    confirmed = _one_sentence(decision) if (decision or "").strip() else ""
    # Held across the agent run so a doc save cannot land inside the restore.
    with st.jira_lock(jira):
        req = _require_ticket(root, jira, ticket_id)
        current = {name: _read(req, name) for name in DOC_FILES}
        proposal_path = req / ".doc-align" / f"{ticket_id}.json"
        snap = _snapshot(req, DOC_FILES + ("STATUS.yaml",))
        qa_before = _capture_tree(req / "qa")
        top_before = _capture_top(req)
        prompt = _prompt(
            jira=jira,
            ticket_id=ticket_id,
            summary=summary.strip(),
            decision=confirmed,
            proposal_path=proposal_path,
        )
        agent = runner or get_runner(root, "doc-align", print_mode=True, spec=DOC_ALIGN_SPEC)
        result: RunResult | None = None
        err: BaseException | None = None
        raw = ""
        outside = False
        try:
            result = agent.start(
                prompt,
                root,
                [req / name for name in DOC_FILES],
            )
        except BaseException as exc:
            err = exc
        finally:
            restored = _restore(req, snap)
            qa_dirty = _restore_tree(req / "qa", qa_before)
            top_dirty = _restore_top(req, top_before)
            raw, proposal_dirty = _take_proposal(proposal_path)
            outside = qa_dirty or top_dirty or proposal_dirty or "STATUS.yaml" in restored
        if outside:
            raise RuntimeError(_OUTSIDE) from err
        if err is not None:
            raise err
        if not isinstance(result, RunResult) or not result.ok:
            summary_text = getattr(result, "summary", "") if result is not None else ""
            raise RuntimeError(summary_text or "按审查意见改文档失败")
        if not raw:
            raise RuntimeError("模型没有写出文档修改提案")
        return _interpret(raw, current, ticket_id)


def apply(
    root: Path,
    jira: str,
    ticket_id: str,
    *,
    decision: str,
    base: dict[str, str],
    files: dict[str, str],
) -> dict[str, Any]:
    """Write a confirmed proposal. Ticket slots and case expectations stay."""
    decided = _one_sentence(decision)
    with st.jira_lock(jira):
        req = _require_ticket(root, jira, ticket_id)
        current = {name: _read(req, name) for name in DOC_FILES}
        hashed = {name: _hash(current[name]) for name in DOC_FILES}
        if {name: str(base.get(name) or "") for name in DOC_FILES} != hashed:
            raise ValueError("文档在生成修改后又被改过，请重新生成")
        proposed = _proposed_files(files)
        _require_same_tickets(current["TICKETS.md"], proposed["TICKETS.md"])
        originals = {name: _read_bytes(req / name) for name in DOC_FILES}
        status_path = req / "STATUS.yaml"
        status_before = _read_bytes(status_path)
        changed: list[str] = []
        try:
            for name in DOC_FILES:
                if _norm(proposed[name]) == _norm(current[name]):
                    continue
                save_doc(root, jira, _SLUG[name], proposed[name])
                changed.append(name)
            status_after = _read_bytes(status_path)
            if status_after != status_before:
                raise ValueError("按审查意见改文档时不应改 STATUS.yaml")
            qa_stale = False
            if any(name in _STALE_ON for name in changed) and iter_case_markdown(
                paths.qa_dir(root, jira) / "cases"
            ):
                mark_stale(paths.qa_dir(root, jira), f"审查意见改文档 {ticket_id}")
                qa_stale = True
        except Exception:
            for name, data in originals.items():
                _put_bytes(req / name, data)
            _put_bytes(status_path, status_before)
            raise
        return {
            "jira": jira,
            "ticket_id": ticket_id,
            "decision": decided,
            "changed": changed,
            "qa_stale": qa_stale,
        }


def _prompt(
    *,
    jira: str,
    ticket_id: str,
    summary: str,
    decision: str,
    proposal_path: Path,
) -> str:
    confirmed = ""
    if decision:
        confirmed = (
            f"已确认的决定：{decision}\n"
            "这句已经由人确认，不要再返回 clarify。按这一句改冲突的地方，status 用 ready。\n"
        )
    return (
        f"Run skill `doc-align` (already loaded via --skill) for {jira}, ticket {ticket_id}.\n"
        "人在复核里写的意见如下。只处理和这四份文档冲突的句子。\n"
        f"{confirmed}"
        f"审查意见：\n{summary}\n\n"
        "读 REQUIREMENT.md、GRILL.md、SPEC.md、TICKETS.md。"
        "意见含糊、会改到不同句子时，返回 clarify，用一句话写出准备采用的做法，不要写 files。\n"
        f"提案只写到这个文件：{proposal_path}\n"
        "不要修改那四份文档、STATUS.yaml、代码或用例。"
    )


def _interpret(raw: str, current: dict[str, str], ticket_id: str) -> dict[str, Any]:
    data = _load_json(raw)
    status = str(data.get("status") or "").strip().lower()
    if status not in {"clarify", "ready"}:
        raise ValueError("提案 status 只能是 clarify 或 ready")
    decision = _one_sentence(str(data.get("decision") or ""))
    base = {name: _hash(current[name]) for name in DOC_FILES}
    if status == "clarify":
        return {
            "ticket_id": ticket_id,
            "status": "clarify",
            "decision": decision,
            "diffs": [],
            "files": {},
            "base": base,
        }
    proposed = _proposed_files(data.get("files"))
    _require_same_tickets(current["TICKETS.md"], proposed["TICKETS.md"])
    diffs = _diffs(current, proposed)
    if not diffs:
        return {
            "ticket_id": ticket_id,
            "status": "noop",
            "decision": decision,
            "diffs": [],
            "files": {},
            "base": base,
        }
    return {
        "ticket_id": ticket_id,
        "status": "ready",
        "decision": decision,
        "diffs": diffs,
        "files": proposed,
        "base": base,
    }


def _proposed_files(files: Any) -> dict[str, str]:
    if not isinstance(files, dict):
        raise ValueError("ready 提案要带齐四份文档全文")
    out: dict[str, str] = {}
    for name in DOC_FILES:
        text = files.get(name)
        if not isinstance(text, str):
            raise ValueError(f"提案缺少 {name}")
        out[name] = text
    return out


def _ticket_key(ticket: Any) -> tuple[Any, ...]:
    return (
        ticket.id,
        ticket.repo,
        tuple(ticket.depends_on),
        ticket.parallel,
        ticket.source,
        ticket.finding,
        ticket.change,
    )


def _require_same_tickets(before: str, after: str) -> None:
    old = [_ticket_key(t) for t in parse_tickets(before)]
    new = [_ticket_key(t) for t in parse_tickets(after)]
    if old != new:
        raise ValueError("提案不能新增、删除或改票的编号和仓、依赖和来源")


def _diffs(current: dict[str, str], proposed: dict[str, str]) -> list[dict[str, str]]:
    diffs: list[dict[str, str]] = []
    for name in DOC_FILES:
        if _norm(current[name]) == _norm(proposed[name]):
            continue
        text = "".join(
            difflib.unified_diff(
                current[name].splitlines(keepends=True),
                proposed[name].splitlines(keepends=True),
                fromfile=name,
                tofile=name,
                n=2,
            )
        )
        if text:
            diffs.append({"file": name, "diff": text})
    return diffs


def _load_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("提案不是 JSON") from e
    if not isinstance(data, dict):
        raise ValueError("提案必须是 JSON 对象")
    return data


def _one_sentence(text: str) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        raise ValueError("决定不能为空")
    if len(clean) > _DECISION_MAX:
        raise ValueError("决定必须是一句话")
    return clean


def _req(root: Path, jira: str) -> Path:
    req = paths.req_dir(root, jira)
    if not req.is_dir() or not paths.is_req_dir(req):
        raise FileNotFoundError(f"no requirement {jira}")
    return req


def _read(req: Path, name: str) -> str:
    path = req / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _read_bytes(path: Path) -> bytes | None:
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_bytes()


def _put_bytes(path: Path, data: bytes | None) -> None:
    if data is None:
        if path.is_symlink() or path.exists():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        return
    atomic_write_bytes(path, data)


def _norm(text: str) -> str:
    if not text:
        return ""
    return text if text.endswith("\n") else text + "\n"


def _hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _capture_tree(root: Path) -> dict[str, bytes] | None:
    """File bytes under qa/, or None when that directory is absent."""
    if not root.exists() and not root.is_symlink():
        return None
    files: dict[str, bytes] = {}
    if root.is_symlink() or not root.is_dir():
        return files
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _restore_tree(root: Path, before: dict[str, bytes] | None) -> bool:
    """Put qa/ back. True when the agent created or edited anything there."""
    if before is None:
        if not root.exists() and not root.is_symlink():
            return False
        if root.is_dir() and not root.is_symlink():
            shutil.rmtree(root)
        else:
            root.unlink()
        return True
    dirty = False
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        root.unlink()
        dirty = True
    root.mkdir(parents=True, exist_ok=True)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or (path.is_file() and rel not in before):
            path.unlink()
            dirty = True
    for rel, data in before.items():
        dest = root / rel
        if dest.is_symlink():
            dest.unlink()
            dirty = True
        current = dest.read_bytes() if dest.is_file() else None
        if current != data:
            atomic_write_bytes(dest, data)
            dirty = True
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            path.rmdir()
    return dirty


def _capture_top(req: Path) -> tuple[dict[str, bytes], set[str]]:
    """Sibling files of the four docs, plus directory names we must not delete."""
    files: dict[str, bytes] = {}
    dirs: set[str] = set()
    for path in req.iterdir():
        if path.name in _MANAGED:
            continue
        if path.is_symlink():
            continue
        if path.is_dir():
            dirs.add(path.name)
        elif path.is_file():
            files[path.name] = path.read_bytes()
    return files, dirs


def _restore_top(req: Path, before: tuple[dict[str, bytes], set[str]]) -> bool:
    files, dirs = before
    dirty = False
    seen: set[str] = set()
    for path in list(req.iterdir()):
        if path.name in _MANAGED:
            continue
        if path.is_dir() and not path.is_symlink():
            if path.name not in dirs:
                shutil.rmtree(path)
                dirty = True
            continue
        seen.add(path.name)
        prev = files.get(path.name)
        if path.is_symlink() or not path.is_file() or prev is None:
            if path.is_symlink() or path.exists():
                path.unlink()
            dirty = True
            continue
        if path.read_bytes() != prev:
            atomic_write_bytes(path, prev)
            dirty = True
    for name, data in files.items():
        if name in seen:
            continue
        atomic_write_bytes(req / name, data)
        dirty = True
    return dirty


def _take_proposal(path: Path) -> tuple[str, bool]:
    """Read the proposal, then remove it. Extra files beside it are a miss."""
    parent = path.parent
    raw = ""
    dirty = False
    if path.is_file() and not path.is_symlink():
        raw = path.read_text(encoding="utf-8")
    elif path.exists() or path.is_symlink():
        dirty = True
    if not parent.is_dir():
        return raw, dirty
    for child in list(parent.iterdir()):
        if child.name == path.name:
            continue
        dirty = True
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    if path.exists() or path.is_symlink():
        path.unlink()
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    return raw, dirty
