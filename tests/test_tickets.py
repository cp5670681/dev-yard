from dev_yard.tickets import parse_tickets, write_import_tickets


def test_parse_tickets():
    text = """
# Tickets

## T1: api
- repo: backend
- depends_on:
- parallel: false

## T2: ui
- repo: frontend
- depends_on: T1
- parallel: true
"""
    ts = parse_tickets(text)
    assert [t.id for t in ts] == ["T1", "T2"]
    assert ts[0].repo == "backend"
    assert ts[1].depends_on == ["T1"]
    assert ts[1].parallel is True


def test_parse_ignores_non_ticket_headings():
    text = """
# Tickets

## 验收
- repo: backend

## T1: real
- repo: backend

## Further Notes
stuff
"""
    ts = parse_tickets(text)
    assert [t.id for t in ts] == ["T1"]
    assert ts[0].repo == "backend"


def test_write_import_tickets_replaces_and_marks_source(tmp_path):
    req = tmp_path / "AB-1"
    req.mkdir()
    (req / "TICKETS.md").write_text(
        "# Tickets — old\n\n## T9: stale\n- repo: gone\n- source: light\n"
    )
    write_import_tickets(
        req,
        [
            {"id": "T1", "repo": "backend", "branch": "feature/x", "base": "abc123"},
            {"id": "T2", "repo": "frontend", "branch": "origin/feature/y"},
        ],
    )
    ts = parse_tickets((req / "TICKETS.md").read_text(encoding="utf-8"))
    assert [t.id for t in ts] == ["T1", "T2"]
    assert all(t.source == "import" for t in ts)
    assert ts[0].repo == "backend"
    assert "stale" not in (req / "TICKETS.md").read_text(encoding="utf-8")
    assert "abc123" in (req / "TICKETS.md").read_text(encoding="utf-8")
