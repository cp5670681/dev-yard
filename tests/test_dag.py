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
