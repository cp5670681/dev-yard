"""Pydantic request models for the JSON API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GrillAnswersIn(BaseModel):
    answers: list[dict[str, Any]] = Field(default_factory=list)


class OpenIn(BaseModel):
    jira: str = ""
    key: str = ""
    source: str = "pi"
    target: str = ""
    payload: str = ""
    force: bool = False


class RepoAddIn(BaseModel):
    alias: str = ""
    url: str
    default_base: str = "main"
    role: str = "svc"
    path: str = ""
    provider: str = ""
    model: str = ""
    test_branch: str = ""


class RepoPiIn(BaseModel):
    provider: str = ""
    model: str = ""
    test_branch: str | None = None


class QaConfigIn(BaseModel):
    active_env: str = "local"
    browser: dict[str, Any] = Field(default_factory=dict)
    design: dict[str, Any] = Field(default_factory=dict)
    workers: list[dict[str, Any]] = Field(default_factory=list)
    envs: dict[str, Any] = Field(default_factory=dict)
    renamed: dict[str, str] = Field(default_factory=dict)
    serialize_accounts: bool = False


class QaCheckEnvIn(BaseModel):
    env: str = ""
    jira: str = ""


class ActionIn(BaseModel):
    ticket_id: str = ""
    force: bool = False
    source: str = "pi"
    remote: str = "origin"
    repos: list[str] | None = None
    strategy: str = "ff-only"
    env: str = ""
    resume: bool | None = None
    approve: bool = False
    redesign: bool = False
    feedback: str = ""
    resolve: bool | None = None
    note: str = ""
    repo: str = ""
    grill: bool = False
    run: bool = False


class QaRerunIn(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    env: str = ""


class AssistantSessionIn(BaseModel):
    route: str = "/"
    jira: str = ""


class AssistantMessageIn(BaseModel):
    text: str
    route: str = ""
    jira: str | None = None


class DocSaveIn(BaseModel):
    body: str = ""


class TicketReviewIn(BaseModel):
    verdict: str
    summary: str = ""
    auto_implement: bool = False


class ContractReviewIn(BaseModel):
    verdict: str
    summary: str = ""
    auto_implement: bool = False
    findings: list[dict[str, Any]] = Field(default_factory=list)


class TestReportIn(BaseModel):
    verdict: str
    body: str
    summary: str = ""
    source: str = ""
    findings: list[dict[str, Any]] = Field(default_factory=list)


class StageModelIn(BaseModel):
    provider: str = ""
    model: str = ""


class PiSettingsIn(BaseModel):
    provider: str = ""
    model: str = ""
    stages: dict[str, StageModelIn] = Field(default_factory=dict)


class GitSettingsIn(BaseModel):
    freeze_branch: str = ""


class DevSettingsIn(BaseModel):
    tdd: bool = True
