from __future__ import annotations

import re

DOC_ALIGN_REASON = "dev-yard doc-align: 只能写提案 JSON（.doc-align/<ticket>.json），不能改文档、用例或代码。"


def is_proposal_write(path: str) -> bool:
    text = path.replace("\\", "/").strip()
    if not text or ".." in text:
        return False
    return bool(re.search(r"(?:^|/)\.doc-align/[^/]+\.json$", text))


def validate_doc_align_write(path: str) -> tuple[bool, str | None]:
    if not is_proposal_write(path):
        return False, f"{DOC_ALIGN_REASON}（命中：{path or '(empty)'}）"
    return True, None

