from datetime import UTC, datetime, timedelta

import pytest

from app import workflow as wf
from app.models import Receiver, Router, SiteRecord, Station, Ticket

NOW = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


def make(router=None, receivers=None, hours=0.0, **kw) -> Station:
    return Station(
        code="TEST", region="eu", country="DEU", owner_group=kw.pop("owner_group", "eu"),
        router=router or Router("TEST-RTR01"),
        receivers=receivers or [Receiver("00", True, True, 33), Receiver("01", True, True, 33)],
        site=SiteRecord("SV-1", "Rooftop", "Grid", "24/7"),
        since=NOW - timedelta(hours=hours) if hours else None, **kw)


def down_rx():
    return [Receiver("00", False, False, 0), Receiver("01", False, False, 0)]


# ---------------------------------------------------------------- classify
@pytest.mark.parametrize("station, kind", [
    (make(), "ok"),
    (make(Router("R", state="offline", wan="—"), down_rx(), 2), "router"),
    (make(Router("R", state="offline", wan="LTE", rsrp=-111, sinr=1), hours=1), "mismatch"),
    (make(receivers=down_rx(), hours=3), "obs"),
    (make(receivers=[Receiver("00", True, True, 33), Receiver("01", False, True, 0)], hours=1), "obs"),
    (make(alerts=["IPSec VPN Tunnel Down"], hours=1), "dmvpn"),
    (make(receivers=[Receiver("00", True, True, 33, 75)], hours=1, heat_events=1), "heat"),
    (make(Router("R", wan="LTE (failover)", rsrp=-100, sinr=5), hours=1), "net"),
    (make(Router("R", wan="LTE", rsrp=-112, sinr=-1), hours=1), "net"),
])
def test_classify(station, kind):
    assert wf.classify(station)["kind"] == kind


def test_obs_subtypes_distinguish_antenna_from_network():
    tracking = make(receivers=[Receiver("00", True, True, 33), Receiver("01", False, True, 0)], hours=1)
    unreachable = make(receivers=[Receiver("00", True, True, 33), Receiver("01", False, False, 0)], hours=1)
    assert wf.classify(tracking)["sub"] == "tracking"
    assert wf.classify(unreachable)["sub"] == "unreachable"
    assert wf.classify(make(receivers=down_rx(), hours=1))["sub"] == "router"


def test_status_offline_when_all_receivers_lose_obs():
    s = make(receivers=[Receiver("00", False, True, 0), Receiver("01", False, True, 0)], hours=1)
    assert wf.status_of(s) == "offline"
    assert wf.status_of(make(alerts=["IPSec VPN Tunnel Down"], hours=1)) == "degraded"


# ---------------------------------------------------------------- 24h Grafana gate
def test_ticket_locked_inside_24h_window():
    s = make(Router("R", state="offline", wan="—"), down_rx(), hours=6)
    d = wf.decision(s, wf.classify(s), NOW)
    assert d["kind"] == "wait"
    assert "02:00 UTC Oct 18" in d["text"]
    assert wf.grafana_check(s, NOW)["auto"]["state"] == "wait"


def test_ticket_allowed_after_24h():
    s = make(Router("R", state="offline", wan="—"), down_rx(), hours=25)
    assert wf.decision(s, wf.classify(s), NOW)["kind"] == "create"
    assert wf.grafana_check(s, NOW)["auto"]["state"] == "done"


def test_open_ticket_means_comment_not_new_ticket():
    s = make(Router("R", state="offline", wan="—"), down_rx(), hours=30,
             tickets=[Ticket("NOC-1", "Escalated to Partner", "x", "Elena Ruiz", NOW)])
    d = wf.decision(s, wf.classify(s), NOW)
    assert d["kind"] == "comment" and d["ticket"] == "NOC-1"
    assert "Elena Ruiz" in d["text"]


def test_closed_ticket_means_open_a_new_one():
    s = make(Router("R", state="offline", wan="—"), down_rx(), hours=30,
             tickets=[Ticket("NOC-1", "Completed", "x", None, NOW)])
    d = wf.decision(s, wf.classify(s), NOW)
    assert d["kind"] == "create" and "NOC-1" in d["text"]


def test_mismatch_needs_no_ticket():
    s = make(Router("R", state="offline", wan="LTE", rsrp=-111, sinr=1), hours=1)
    assert wf.decision(s, wf.classify(s), NOW)["kind"] == "none"


def test_heat_only_tickets_when_recurring():
    hot = [Receiver("00", True, True, 33, 75)]
    once = make(receivers=hot, hours=10, heat_events=1)
    recurring = make(receivers=hot, hours=10, heat_events=4)
    assert wf.decision(once, wf.classify(once), NOW)["kind"] == "wait"
    assert wf.decision(recurring, wf.classify(recurring), NOW)["kind"] == "create"


# ---------------------------------------------------------------- checklist
def test_router_workflow_has_partner_sla_step_only_for_sla_partners():
    s = make(Router("R", state="offline", wan="—"), down_rx(), hours=6, owner_group="apac24")
    ids = [x["id"] for x in wf.build_workflow(s, wf.classify(s), NOW)["steps"]]
    assert "sla" in ids and "g24" in ids
    s.owner_group = "eu"
    assert "sla" not in [x["id"] for x in wf.build_workflow(s, wf.classify(s), NOW)["steps"]]


def test_manual_progress_is_applied():
    s = make(Router("R", state="offline", wan="—"), down_rx(), hours=6, progress={"chan": True})
    steps = wf.resolve_steps(wf.build_workflow(s, wf.classify(s), NOW)["steps"], s.progress)
    chan = next(x for x in steps if x["id"] == "chan")
    assert chan["state"] == "done" and chan["manual"]


def test_findings_text_lists_checks():
    s = make(Router("R", state="offline", wan="—"), down_rx(), hours=6, alerts=["WAN link down"])
    text = wf.findings_text(s, NOW)
    assert text.startswith("Workflow: Router is offline")
    assert "[x] Check NetCloud activity" in text


@pytest.mark.parametrize("hours, expected", [(0.5, "30m"), (6, "6h"), (6.5, "6h 30m"), (72, "3d")])
def test_fmt_hours(hours, expected):
    assert wf.fmt_hours(hours) == expected
