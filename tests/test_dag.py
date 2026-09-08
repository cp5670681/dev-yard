from pathlib import Path

from dev_yard import status as st
from dev_yard.tickets import Ticket


def test_refresh_ready(tmp_path: Path):
    tickets = [
        Ticket("T1", "a", "be"),
        Ticket("T2", "b", "fe", depends_on=["T1"]),
    ]
    data = {"jira": "X-1", "phase": "open", "tickets": {}}
    st.sync_tickets(data, tickets)
    st.refresh_ready(data)
    assert data["tickets"]["T1"]["state"] == "ready"
    assert data["tickets"]["T2"]["state"] == "pending"
    data["tickets"]["T1"]["state"] = "done"
    st.refresh_ready(data)
    assert data["tickets"]["T2"]["state"] == "ready"


def test_sync_tickets_coerces_list_of_id_repo_maps():
    tickets = [
        Ticket("T1", "a", "research"),
        Ticket("T2", "b", "research-front"),
    ]
    data = {
        "jira": "PG-1",
        "phase": "grilled",
        "tickets": [{"T1": "research"}, {"T2": "research-front"}],
    }
    st.sync_tickets(data, tickets)
    assert data["tickets"]["T1"]["repo"] == "research"
    assert data["tickets"]["T1"]["state"] == "pending"
    assert data["tickets"]["T2"]["repo"] == "research-front"


def test_load_coerces_ticket_list(tmp_path: Path):
    d = tmp_path / "reqs" / "PG-1"
    d.mkdir(parents=True)
    (d / "STATUS.yaml").write_text(
        "jira: PG-1\nphase: grilled\ntickets:\n  - T1: research\n  - T2: research-front\n"
    )
    data = st.load(tmp_path, "PG-1")
    assert data["tickets"]["T1"]["repo"] == "research"
    assert data["tickets"]["T2"]["repo"] == "research-front"


def test_sync_tickets_drops_orphans():
    tickets = [Ticket("T2", "b", "be")]
    data = {
        "jira": "X-1",
        "phase": "frozen",
        "tickets": {
            "T1": {"state": "ready", "repo": "be"},
            "T2": {"state": "pending", "repo": "be"},
        },
    }
    st.sync_tickets(data, tickets)
    assert "T1" not in data["tickets"]
    assert data["tickets"]["T2"]["repo"] == "be"


def test_ready_ids_include_implementing():
    data = {
        "tickets": {
            "T1": {"state": "implementing", "repo": "be"},
            "T2": {"state": "ready", "repo": "be"},
            "T3": {"state": "done", "repo": "be"},
        }
    }
    assert st.ready_ids(data) == ["T1", "T2"]
