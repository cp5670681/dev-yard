"""Settings / repo JSON API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from dev_yard import paths
from dev_yard import service as yard_service
from dev_yard.config import DevSettings, GitSettings, save_dev_settings, save_git_settings
from dev_yard.qa_config import QaConfigUnreadable
from dev_yard.qa_config import TestRejected as QaConfigRejected
from dev_yard.web.context import AppContext
from dev_yard.web.schemas import (
    DevSettingsIn,
    GitSettingsIn,
    PiSettingsIn,
    QaCheckEnvIn,
    QaConfigIn,
    RepoAddIn,
    RepoPiIn,
)


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/pi")
    def api_pi_get():
        return ctx.pi_settings_out()

    @router.put("/api/pi")
    def api_pi_put(payload: PiSettingsIn):
        try:
            return ctx.save_pi(payload)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.get("/api/git")
    def api_git_get():
        try:
            return ctx.git_settings_out()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.put("/api/git")
    def api_git_put(payload: GitSettingsIn):
        try:
            save_git_settings(ctx.root, GitSettings(freeze_branch=payload.freeze_branch))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return ctx.git_settings_out()

    @router.get("/api/dev")
    def api_dev_get():
        try:
            return ctx.dev_settings_out()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.put("/api/dev")
    def api_dev_put(payload: DevSettingsIn):
        try:
            save_dev_settings(ctx.root, DevSettings(tdd=payload.tdd))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return ctx.dev_settings_out()

    @router.get("/api/qa-config")
    def api_qa_config_get():
        return ctx.qa_config_out()

    @router.put("/api/qa-config")
    def api_qa_config_put(payload: QaConfigIn):
        try:
            return ctx.save_qa(payload.model_dump())
        except QaConfigUnreadable as e:
            raise HTTPException(409, str(e)) from e
        except QaConfigRejected as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/api/qa-check-env")
    def api_qa_check_env(payload: QaCheckEnvIn):
        from dev_yard.script_exec import ExecUnreachable, check_env

        worktree = None
        key = payload.jira.strip()
        if key:
            wt_root = paths.req_dir(ctx.root, key) / "worktrees"
            if wt_root.is_dir():
                for child in sorted(wt_root.iterdir()):
                    if child.is_dir():
                        worktree = child
                        break
        try:
            return check_env(
                ctx.root,
                env_name=payload.env.strip() or None,
                jira=key or None,
                worktree=worktree,
            )
        except (QaConfigRejected, ExecUnreachable, FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    @router.get("/api/repos")
    def api_repos():
        return ctx.list_repos()

    @router.post("/api/repos")
    def api_repos_add(payload: RepoAddIn):
        try:
            submitted = ctx.add_repo(payload)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return ctx.jobs_out([submitted])

    @router.put("/api/repos/{alias}")
    def api_repos_set_pi(alias: str, payload: RepoPiIn):
        try:
            yard_service.repo_set_pi(
                ctx.root,
                alias,
                payload.provider.strip() or None,
                payload.model.strip() or None,
                test_branch=payload.test_branch,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return ctx.list_repos()

    return router
