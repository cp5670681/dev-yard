from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROUND_FILE = ".grill-round.json"
CUSTOM = "__custom__"
WEB_GRILL_MARKER = "WEB_GRILL_ROUND"
MAX_ROUNDS = 12


def web_grill_extra(jira: str) -> str:
    return (
        f"{WEB_GRILL_MARKER}: follow grill-with-docs {WEB_GRILL_MARKER} "
        f"(read WEB-ROUND.md). Write `reqs/{jira}/{ROUND_FILE}` for this frontier, "
        "then stop. Do not invent user answers."
    )


@dataclass
class Option:
    id: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label}


@dataclass
class Question:
    id: str
    title: str
    body: str = ""
    options: list[Option] = field(default_factory=list)
    suggested: str | None = None
    suggested_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "options": [o.to_dict() for o in self.options],
            "suggested_text": self.suggested_text,
        }
        if self.suggested:
            data["suggested"] = self.suggested
        return data


@dataclass
class GrillRound:
    done: bool = False
    round: int = 1
    intro: str = ""
    questions: list[Question] = field(default_factory=list)

    def awaiting(self) -> bool:
        return (not self.done) and bool(self.questions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "done": self.done,
            "round": self.round,
            "intro": self.intro,
            "questions": [q.to_dict() for q in self.questions],
        }


def round_path(req: Path) -> Path:
    return req / ROUND_FILE


def parse_round(data: Any) -> GrillRound:
    if not isinstance(data, dict):
        raise ValueError("grill round must be an object")
    questions: list[Question] = []
    raw_qs = data.get("questions") or []
    if not isinstance(raw_qs, list):
        raw_qs = []
    for i, raw in enumerate(raw_qs):
        if not isinstance(raw, dict):
            continue
        questions.append(_parse_question(raw, i))
    try:
        n = int(data.get("round") or 1)
    except (TypeError, ValueError):
        n = 1
    done = bool(data.get("done"))
    return GrillRound(
        done=done,
        round=max(n, 1),
        intro=str(data.get("intro") or ""),
        questions=[] if done else questions,
    )


def load_round(req: Path) -> GrillRound | None:
    path = round_path(req)
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = None
        if data is not None:
            return parse_round(data)
    grill = req / "GRILL.md"
    if grill.is_file():
        return parse_markdown(grill.read_text())
    return None


def parse_markdown(text: str) -> GrillRound | None:
    matches = list(re.finditer(r"(?m)^##\s+(.*)$", text))
    if matches:
        heading = matches[-1].group(1)
        body = text[matches[-1].end() :]
        if "❓" not in body:
            return None
        if re.search(r"answers", heading, re.I) and not re.search(r"frontier", heading, re.I):
            return None
        n = 1
        found = re.search(r"Round\s+(\d+)", heading, re.I)
        if found:
            n = int(found.group(1))
        return _questions_from_markdown(n, body)
    if "❓" not in text:
        return None
    return _questions_from_markdown(1, text)


def format_answers(rnd: GrillRound, answers: list[dict[str, Any]]) -> str:
    by_id = {
        str(a.get("id")): a
        for a in answers
        if isinstance(a, dict) and a.get("id")
    }
    lines = [f"## Round {rnd.round} — answers", ""]
    for q in rnd.questions:
        a = by_id.get(q.id) or {}
        option = str(a.get("option") or "") or None
        text = str(a.get("text") or "").strip()
        if not option and not text:
            option = q.suggested
            text = q.suggested_text
        lines.append(f"- **{q.id} {q.title}**：{_answer_line(q, option, text)}")
    return "\n".join(lines).rstrip() + "\n"


def apply_answers(req: Path, rnd: GrillRound, answers: list[dict[str, Any]]) -> None:
    md = req / "GRILL.md"
    existing = md.read_text() if md.is_file() else ""
    block = format_answers(rnd, answers)
    md.write_text((existing.rstrip() + "\n\n" + block).strip() + "\n")
    path = round_path(req)
    if path.exists():
        path.unlink()


def _parse_question(raw: dict[str, Any], index: int) -> Question:
    qid = str(raw.get("id") or f"Q{index + 1}")
    title = str(raw.get("title") or qid)
    body = str(raw.get("body") or "")
    options = _parse_options(raw.get("options") or [])
    suggested = raw.get("suggested")
    suggested_s = str(suggested) if suggested else None
    if suggested_s and suggested_s != CUSTOM and options:
        ids = {o.id for o in options}
        if suggested_s not in ids:
            hit = next((o.id for o in options if o.label == suggested_s), None)
            suggested_s = hit
    suggested_text = str(raw.get("suggested_text") or "")
    if not suggested_text and suggested_s and suggested_s != CUSTOM:
        hit = next((o for o in options if o.id == suggested_s), None)
        if hit:
            suggested_text = hit.label
    return Question(
        id=qid,
        title=title,
        body=body,
        options=options,
        suggested=suggested_s,
        suggested_text=suggested_text,
    )


def _parse_options(raw: Any) -> list[Option]:
    if not isinstance(raw, list):
        return []
    out: list[Option] = []
    for j, opt in enumerate(raw):
        fallback = chr(65 + j) if j < 26 else str(j + 1)
        if isinstance(opt, str):
            out.append(Option(id=fallback, label=opt))
        elif isinstance(opt, dict):
            oid = str(opt.get("id") or fallback)
            label = str(opt.get("label") or opt.get("text") or oid)
            out.append(Option(id=oid, label=label))
    return out


def _questions_from_markdown(round_n: int, body: str) -> GrillRound | None:
    chunks = re.split(r"❓\s*", body)
    intro = chunks[0].strip()
    questions: list[Question] = []
    for i, chunk in enumerate(chunks[1:]):
        rec_m = re.search(r"➡️\s*(.*)", chunk, re.S)
        rec = rec_m.group(1).strip() if rec_m else ""
        qtext = chunk[: rec_m.start()] if rec_m else chunk
        qtext = qtext.strip()
        first, _, rest = qtext.partition("\n")
        first = first.strip().strip("*").strip()
        id_m = re.match(r"\*{0,2}(Q\d+)\*{0,2}\s*[-—]?\s*(.*)", first)
        if id_m:
            qid = id_m.group(1)
            title = id_m.group(2).strip(" *:：")
        else:
            qid = f"Q{i + 1}"
            title = first
        qbody = rest.strip()
        options: list[Option] = []
        for om in re.finditer(r"(?m)^[-*]\s*(?:选\s*)?([A-Z])[:：]\s*(.+)$", qbody):
            options.append(Option(id=om.group(1), label=om.group(2).strip()))
        suggested = None
        sm = re.match(r"选\s*([A-Z])", rec)
        if sm:
            suggested = sm.group(1)
        questions.append(
            Question(
                id=qid,
                title=title or qid,
                body=qbody,
                options=options,
                suggested=suggested,
                suggested_text=rec,
            )
        )
    if not questions:
        return None
    return GrillRound(done=False, round=round_n, intro=intro, questions=questions)


def _answer_line(q: Question, option: str | None, text: str) -> str:
    if option == CUSTOM or (not option and text):
        return f"自定义：{text or q.suggested_text or '—'}"
    if option:
        label = next((o.label for o in q.options if o.id == option), "")
        extra = text or (q.suggested_text if option == q.suggested else "")
        bit = f"选 {option}"
        if label:
            bit += f" — {label}"
        if extra and extra != label:
            bit += f"。{extra}"
        return bit
    return text or q.suggested_text or "—"
