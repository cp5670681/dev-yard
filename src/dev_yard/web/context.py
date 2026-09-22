"""Shared state and payload builders for the web console routes.

`create_app` used to be a ~1000-line closure holding `root`, the job runner,
the assistant hub and dozens of helpers. They live here so route modules can
stay small and testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import markdown
from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from dev_yard import paths
from dev_yard import service as yard_service
from dev_yard.actions import ACTION_LABELS, JOB_ACTIONS
from dev_yard.assistant import AssistantHub
from dev_yard.config import (
    PI_STAGES,
    DevSettings,
    GitSettings,
    PiSettings,
    StageModel,
    git_settings_out,
    load_dev_settings,
    load_git_settings,
    load_pi_settings,
    save_pi_settings,
)
from dev_yard.gitops import GitError
from dev_yard.pi_catalog import list_pi_catalog
from dev_yard.pi_session import cwd_is_under_root
from dev_yard.qa_config import (
    QaConfigUnreadable,
    qa_payload,
    redact_qa_yaml,
    save_qa_config,
)
from dev_yard.qa_config import TestRejected as QaConfigRejected
from dev_yard.web.board import (
    DOC_FILES,
    ReqDetail,
    asset_file,
    list_repos,
    requirement_detail,
    save_doc,
)
from dev_yard.web.jobs import Job, JobRunner, _pi_run_until
from dev_yard.web.sanitize import sanitize_html

_ASSET_SRC = re.compile(r'src=(["\'])(?:\./)?assets/([^"\']+)\1')

HERE = Path(__file__).parent
SPA = HERE / "spa"
STATIC = HERE / "static"


def spa_index() -> Any:
    from fastapi.responses import FileResponse

    index = SPA / "index.html"
    if not index.is_file():
        raise HTTPException(503, "frontend not built; run pnpm --dir web build")
    return FileResponse(index)


def render_markdown(text: str, jira: str) -> str:
    html = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br"],
    )
    html = sanitize_html(html)
    quoted = quote(jira, safe="")

    def repl(m: re.Match[str]) -> str:
        q, name = m.group(1), Path(m.group(2)).name
        return f"src={q}/r/{quoted}/assets/{quote(name)}{q}"

    return _ASSET_SRC.sub(repl, html)


@dataclass
class AppContext:
    root: Path
    jobs: JobRunner
    assistants: AssistantHub
    templates: Jinja2Templates

    # ---- templates -------------------------------------------------------
    def base(self, request: Request, **extra: Any) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "request": request,
            "root": str(self.root),
            "root_name": self.root.name,
            "running_jobs": self.jobs.running_brief(),
        }
        ctx.update(extra)
        return ctx

    # ---- requirement helpers --------------------------------------------
    def detail_or_404(self, jira: str) -> ReqDetail:
        detail = requirement_detail(self.root, jira)
        if detail is None:
            raise HTTPException(404, f"no requirement {jira}")
        return detail

    def action_labels(self) -> dict[str, str]:
        """Builtin labels merged with plugin stage titles."""
        from dev_yard.stages import load_registry

        labels = dict(ACTION_LABELS)
        for spec in load_registry(self.root).values():
            if not spec.builtin and spec.title:
                labels[spec.name] = spec.title
        return labels

    def known_action(self, action: str) -> bool:
        if action in JOB_ACTIONS:
            return True
        from dev_yard.stages import load_registry

        spec = load_registry(self.root).get(action)
        return spec is not None and not spec.builtin

    def jobs_out(self, submitted: list[Job]) -> dict[str, Any]:
        return {"jobs": [j.snapshot() for j in submitted]}

    def submit_action(
        self,
        action: str,
        jira: str,
        ids: list[str] | None,
        extra: dict[str, Any],
    ) -> list[Job]:
        jobs = self.jobs
        root = self.root
        if action in {"implement", "review", "fix-contract", "fix-test"}:
            busy = jobs.busy_tickets(jira, action)
            if busy is None:
                raise ValueError(f"{jira} already has a running job")
            if not ids:
                detail = self.detail_or_404(jira)
                if action == "fix-contract":
                    wanted = yard_service.prepare_fix_tickets(root, jira, "contract")
                elif action == "fix-test":
                    wanted = yard_service.prepare_fix_tickets(root, jira, "test")
                else:
                    wanted = [
                        t.id
                        for t in detail.tickets
                        if (t.can_implement if action == "implement" else t.can_review)
                    ]
                wanted = [tid for tid in wanted if tid not in busy]
                if not wanted:
                    raise ValueError(
                        f"{jira} already has a running job ({action})"
                        if busy
                        else (
                            "没有处于待审查或阻断状态的票"
                            if action == "review"
                            else "没有可运行的票"
                        )
                    )
                ids = wanted
            else:
                ids = [tid for tid in ids if tid not in busy]
                if not ids:
                    raise ValueError(f"{jira} already has a running job ({action})")
            claimed, previous = yard_service.claim_run(root, jira, action, ids)
            if not claimed:
                raise ValueError(
                    "没有处于待审查或阻断状态的票"
                    if action == "review"
                    else "没有可运行的票"
                )
            submitted: list[Job] = []
            queued: set[str] = set()
            try:
                extra = {**extra, "label": self.action_labels().get(action, action)}
                for tid in claimed:
                    job = jobs.submit(action, jira, ticket_ids=[tid], extra=extra)
                    submitted.append(job)
                    queued.add(tid)
            except Exception:
                leftover = {
                    tid: previous[tid]
                    for tid in claimed
                    if tid not in queued and tid in previous
                }
                yard_service.restore_claim(root, jira, leftover)
                raise
            return submitted
        if action == "reset-grill":
            # Preempt a waiting grill job first: it is blocked on the very round
            # file this reset deletes, and would otherwise re-apply it on submit.
            jobs.cancel_grill(jira)
        extra = {**extra, "label": self.action_labels().get(action, action)}
        return [jobs.submit(action, jira, ticket_ids=ids, extra=extra)]

    def requirement_payload(self, detail: ReqDetail) -> dict[str, Any]:
        return {
            "jira": detail.jira,
            "title": detail.title,
            "phase": detail.phase,
            "next": detail.next_label,
            "contract": detail.contract,
            "contract_summary": detail.contract_summary,
            "contract_summary_html": (
                render_markdown(detail.contract_summary, detail.jira)
                if detail.contract_summary
                else ""
            ),
            "test": detail.test,
            "repos": detail.repos,
            "worktrees": detail.worktrees,
            "assets": detail.assets,
            "steps": [
                {"id": s.id, "done": s.done, "current": s.current} for s in detail.steps
            ],
            "tickets": [
                {
                    "id": t.id,
                    "title": t.title,
                    "repo": t.repo,
                    "state": t.state,
                    "depends_on": t.depends_on,
                    "parallel": t.parallel,
                    "can_implement": t.can_implement,
                    "can_review": t.can_review,
                    "last_summary": t.last_summary,
                    "last_summary_html": (
                        render_markdown(t.last_summary, detail.jira)
                        if t.last_summary
                        else ""
                    ),
                    "source": t.source,
                    "finding": t.finding,
                }
                for t in detail.tickets
            ],
            "actions": [
                {"id": a.id, "label": a.label, "enabled": a.enabled, "reason": a.reason}
                for a in detail.actions
            ],
            "docs": [
                {
                    "slug": d.slug,
                    "filename": d.filename,
                    "filled": d.filled,
                    "exists": d.exists,
                }
                for d in detail.docs
            ],
            "stage_runs": detail.stage_runs,
            "qa": detail.qa,
            "branch": detail.branch,
            "changes": detail.changes,
        }

    def doc_payload(self, detail: ReqDetail, slug: str) -> dict[str, Any]:
        doc = next((d for d in detail.docs if d.slug == slug), None)
        if doc is None:
            raise HTTPException(404, f"unknown doc {slug}")
        return {
            "jira": detail.jira,
            "slug": doc.slug,
            "filename": doc.filename,
            "filled": doc.filled,
            "exists": doc.exists,
            "text": doc.text,
            "html": render_markdown(doc.text, detail.jira),
        }

    # ---- settings payloads ----------------------------------------------
    def pi_settings_out(self) -> dict[str, Any]:
        s = load_pi_settings(self.root)
        stages = {
            name: {
                "provider": (s.stages.get(name).provider if name in s.stages else None)
                or "",
                "model": (s.stages.get(name).model if name in s.stages else None) or "",
            }
            for name in PI_STAGES
        }
        return {
            "provider": s.provider or "",
            "model": s.model or "",
            "stages": stages,
            "stage_ids": list(PI_STAGES),
            "catalog": list_pi_catalog(),
        }

    def save_pi(self, payload: Any) -> dict[str, Any]:
        unknown = [n for n in payload.stages if n not in PI_STAGES]
        if unknown:
            raise ValueError(f"unknown pi stages: {', '.join(unknown)}")
        save_pi_settings(
            self.root,
            PiSettings(
                provider=payload.provider.strip() or None,
                model=payload.model.strip() or None,
                stages={
                    name: StageModel(
                        provider=entry.provider.strip() or None,
                        model=entry.model.strip() or None,
                    )
                    for name, entry in payload.stages.items()
                },
            ),
        )
        return self.pi_settings_out()

    def git_settings_out(self) -> dict[str, Any]:
        return git_settings_out(load_git_settings(self.root))

    def dev_settings_out(self) -> dict[str, Any]:
        return {"tdd": load_dev_settings(self.root).tdd}

    def qa_config_out(self) -> dict[str, Any]:
        """Form state for qa.yaml.

        A file that cannot be parsed still answers 200 with `payload: null` and
        the raw text, so the page can show you what to fix instead of 500ing.
        """
        path = paths.qa_yaml(self.root)
        exists = path.is_file()
        try:
            payload: dict[str, Any] | None = qa_payload(self.root)
            parse_error = ""
        except QaConfigRejected as e:
            payload = None
            # YAML errors echo the offending line, which may hold a secret.
            parse_error = redact_qa_yaml(str(e))
        raw = ""
        if exists:
            try:
                raw = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw = path.read_bytes().decode("utf-8", errors="replace")
            except OSError as e:
                raw = f"(cannot read qa.yaml: {e})"
            raw = redact_qa_yaml(raw)
        return {
            "exists": exists,
            "parse_error": parse_error,
            "raw": raw,
            # No form to fill in when the file is unreadable, so skip the
            # `pi --list-models` subprocess.
            "payload": payload,
            "catalog": list_pi_catalog()
            if payload is not None
            else {"providers": [], "error": None},
        }

    def save_qa(self, payload: dict[str, Any]) -> dict[str, Any]:
        save_qa_config(self.root, payload)
        return self.qa_config_out()

    def list_repos(self) -> list[dict[str, str]]:
        return list_repos(self.root)

    def save_doc(self, jira: str, slug: str, text: str) -> Path:
        return save_doc(self.root, jira, slug, text)

    def asset_file(self, jira: str, name: str) -> Path:
        return asset_file(self.root, jira, name)

    def add_repo(self, payload: Any) -> Job:
        return self.jobs.submit(
            "repo_add",
            "_repo_",
            extra={
                "alias": payload.alias.strip(),
                "url": payload.url.strip(),
                "default_base": payload.default_base.strip() or "main",
                "role": payload.role.strip() or "svc",
                "path": payload.path.strip() or None,
                "provider": payload.provider.strip() or None,
                "model": payload.model.strip() or None,
                "test_branch": payload.test_branch.strip() or None,
            },
        )

    # ---- assistant helpers ----------------------------------------------
    def assistant_or_404(self, session_id: str) -> Any:
        session = self.assistants.get(session_id)
        if session is None:
            raise HTTPException(404, "unknown assistant session")
        return session

    def pi_run_or_404(self, job_id: str, run_index: int) -> tuple[Any, ...]:
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        snap = job.snapshot()
        runs = snap.get("pi_runs") or []
        if run_index < 0 or run_index >= len(runs):
            raise HTTPException(404, "unknown pi run")
        run = runs[run_index]
        cwd = Path(run["cwd"])
        if not cwd_is_under_root(cwd, self.root):
            raise HTTPException(404, "unknown pi run")
        return job, snap, runs, run

    def load_run_conversation(self, runs: list[dict[str, Any]], index: int, offset: int):
        from dev_yard.pi_session import load_conversation

        return load_conversation(
            Path(runs[index]["cwd"]),
            root=self.root,
            started_at=runs[index].get("started_at"),
            until=_pi_run_until(runs, index),
            offset=offset,
        )


__all__ = [
    "AppContext",
    "DOC_FILES",
    "DevSettings",
    "GitError",
    "GitSettings",
    "HERE",
    "QaConfigRejected",
    "QaConfigUnreadable",
    "SPA",
    "STATIC",
    "render_markdown",
    "spa_index",
]
