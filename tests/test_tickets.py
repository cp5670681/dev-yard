from dev_yard.tickets import parse_tickets


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
