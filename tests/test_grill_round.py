import json
from pathlib import Path

import pytest

from dev_yard.grill_round import (
    CUSTOM,
    apply_answers,
    format_answers,
    load_round,
    load_round_file,
    parse_markdown,
    parse_round,
)


def test_parse_round_json_choice_and_fill():
    rnd = parse_round(
        {
            "done": False,
            "round": 2,
            "intro": "API 契约",
            "questions": [
                {
                    "id": "Q1",
                    "title": "参数命名",
                    "body": "follow_by 现在是团队名",
                    "options": [
                        {"id": "A", "label": "保留 follow_by"},
                        {"id": "B", "label": "改名 team_names"},
                    ],
                    "suggested": "B",
                    "suggested_text": "改成 team_names + follow_members",
                },
                {
                    "id": "Q2",
                    "title": "文案",
                    "body": "Alert 怎么写",
                    "options": [],
                    "suggested_text": "跟进人：全部",
                },
            ],
        }
    )
    assert rnd.awaiting()
    assert rnd.round == 2
    assert rnd.questions[0].suggested == "B"
    assert rnd.questions[1].options == []
    assert rnd.questions[1].suggested_text == "跟进人：全部"


def test_done_round_drops_questions():
    rnd = parse_round(
        {
            "done": True,
            "round": 3,
            "questions": [{"id": "Q1", "title": "x"}],
        }
    )
    assert not rnd.awaiting()
    assert rnd.questions == []


def test_load_round_prefers_json_file(tmp_path: Path):
    req = tmp_path / "reqs" / "AB-1"
    req.mkdir(parents=True)
    (req / "GRILL.md").write_text("# Grill\n\n❓ **Q9** - **from md**：no\n\n➡️ skip\n")
    (req / ".grill-round.json").write_text(
        json.dumps(
            {
                "round": 1,
                "questions": [
                    {
                        "id": "Q1",
                        "title": "from json",
                        "options": [{"id": "A", "label": "yes"}],
                        "suggested": "A",
                    }
                ],
            }
        )
    )
    rnd = load_round(req)
    assert rnd is not None
    assert rnd.questions[0].title == "from json"


def test_load_round_file_ignores_markdown(tmp_path: Path):
    req = tmp_path / "reqs" / "AB-1"
    req.mkdir(parents=True)
    (req / "GRILL.md").write_text("# Grill\n\n❓ **Q9** - **from md**：no\n\n➡️ skip\n")
    assert load_round_file(req) is None
    rnd = load_round(req)
    assert rnd is not None
    assert rnd.questions[0].id == "Q9"


def test_parse_markdown_last_frontier():
    text = """# Grill

## Round 1 — frontier

先定范围。

❓ **Q1** - **范围**：只做这张票？
- 选 A：只这张票
- 选 B：整条史诗

➡️ 选 A。只这张票。

## Round 1 — answers

- **Q1 范围**：选 A

## Round 2 — frontier

接口。

❓ **Q1 — 参数命名**
当前 follow_by 是团队名。
- 选 A：保留
- 选 B：改名

➡️ 选 B。
"""
    rnd = parse_markdown(text)
    assert rnd is not None
    assert rnd.round == 2
    assert rnd.intro.startswith("接口")
    assert rnd.questions[0].id == "Q1"
    assert rnd.questions[0].suggested == "B"
    assert [o.id for o in rnd.questions[0].options] == ["A", "B"]


def test_parse_markdown_ignores_trailing_answers():
    text = """## Round 1 — frontier

❓ **Q1** - **范围**：x

➡️ 选 A。

## Round 1 — answers

- **Q1 范围**：选 A — 只这张票
"""
    assert parse_markdown(text) is None


def test_apply_answers_appends_and_removes_json(tmp_path: Path):
    req = tmp_path / "AB-2"
    req.mkdir()
    (req / "GRILL.md").write_text("# Grill — AB-2\n\n## Round 1 — frontier\n\n❓ Q1\n")
    (req / ".grill-round.json").write_text("{}")
    rnd = parse_round(
        {
            "round": 1,
            "questions": [
                {
                    "id": "Q1",
                    "title": "范围",
                    "options": [
                        {"id": "A", "label": "只这张票"},
                        {"id": "B", "label": "整条史诗"},
                    ],
                    "suggested": "A",
                    "suggested_text": "只这张票",
                },
                {"id": "Q2", "title": "备注", "suggested_text": "无"},
            ],
        }
    )
    apply_answers(
        req,
        rnd,
        [
            {"id": "Q1", "option": "A", "text": ""},
            {"id": "Q2", "option": CUSTOM, "text": "自己写的"},
        ],
    )
    body = (req / "GRILL.md").read_text()
    assert "## Round 1 — answers" in body
    assert "选 A — 只这张票" in body
    assert "自定义：自己写的" in body
    assert not (req / ".grill-round.json").exists()


def test_format_answers_uses_suggestion_when_blank():
    rnd = parse_round(
        {
            "round": 1,
            "questions": [
                {
                    "id": "Q1",
                    "title": "范围",
                    "options": [{"id": "A", "label": "只这张票"}],
                    "suggested": "A",
                    "suggested_text": "只这张票",
                }
            ],
        }
    )
    text = format_answers(rnd, [])
    assert "选 A" in text


def test_clear_round_removes_pending_file(tmp_path: Path):
    from dev_yard.grill_round import clear_round

    req = tmp_path / "AB-7"
    req.mkdir()
    assert clear_round(req) is False
    (req / ".grill-round.json").write_text("{}")
    assert clear_round(req) is True
    assert not (req / ".grill-round.json").exists()


def test_req_reset_grill_drops_round_and_doc(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    from dev_yard import status as st
    from dev_yard.service import GRILL_SKELETON, init_yard, req_open, req_reset_grill

    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-9", source="none")
    (d / ".grill-round.json").write_text(
        json.dumps({"round": 1, "questions": [{"id": "Q1", "title": "x"}]})
    )
    (d / "GRILL.md").write_text("# Grill — AB-9\n\n## Round 1 — answers\n\n- 选 A\n")
    data = st.load(yard, "AB-9")
    data["stage_runs"] = {"grill": {"ok": True}, "spec": {"ok": True}}
    st.save(yard, "AB-9", data)

    req_reset_grill(yard, "AB-9")

    assert not (d / ".grill-round.json").exists()
    assert (d / "GRILL.md").read_text() == GRILL_SKELETON.format(key="AB-9")
    reloaded = st.load(yard, "AB-9")
    assert "grill" not in (reloaded.get("stage_runs") or {})
    assert reloaded["stage_runs"] == {"spec": {"ok": True}}
    assert reloaded["phase"] == "open"


def test_req_reset_grill_missing_requirement(tmp_path: Path):
    from dev_yard.service import init_yard, req_reset_grill

    yard = tmp_path / "yard"
    init_yard(yard)
    with pytest.raises(FileNotFoundError):
        req_reset_grill(yard, "AB-404")


def test_cli_reset_grill(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    from typer.testing import CliRunner

    from dev_yard.cli import app
    from dev_yard.service import init_yard, req_open

    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-10", source="none")
    (d / ".grill-round.json").write_text("{}")
    monkeypatch.chdir(yard)
    res = CliRunner().invoke(app, ["req", "reset-grill", "AB-10", "-y"])
    assert res.exit_code == 0
    assert "对齐已重置" in res.stdout
    assert not (d / ".grill-round.json").exists()
