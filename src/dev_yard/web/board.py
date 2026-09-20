"""Compatibility shim: requirement/board logic now lives in `dev_yard.reqboard`.

Kept so existing imports (`dev_yard.web.board`) keep working.
"""

from __future__ import annotations

from dev_yard.reqboard import (
    BUILTIN_ACTION_IDS,
    DOC_FILES,
    PIPELINE,
    Action,
    DocView,
    ReqDetail,
    ReqSummary,
    Step,
    TicketView,
    asset_file,
    available_actions,
    list_repos,
    list_requirements,
    parse_requirement_title,
    requirement_detail,
    save_doc,
)

__all__ = [
    "BUILTIN_ACTION_IDS",
    "DOC_FILES",
    "PIPELINE",
    "Action",
    "DocView",
    "ReqDetail",
    "ReqSummary",
    "Step",
    "TicketView",
    "asset_file",
    "available_actions",
    "list_requirements",
    "list_repos",
    "parse_requirement_title",
    "requirement_detail",
    "save_doc",
]
