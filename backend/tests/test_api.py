import random

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.notifier import MockSlackNotifier
from app.simulator import tick
from app.store import StationStore, utcnow


@pytest.fixture
def ctx():
    store, notifier = StationStore(), MockSlackNotifier()
    return TestClient(create_app(store, notifier, simulate=False)), store, notifier


def test_health(ctx):
    c, *_ = ctx
    assert c.get("/api/health").json()["mode"] == "mock"


def test_every_station_renders(ctx):
    c, *_ = ctx
    stations = c.get("/api/stations").json()
    assert len(stations) >= 10
    assert {s["status"] for s in stations} == {"ok", "degraded", "offline"}
    for s in stations:
        body = c.get(f"/api/stations/{s['code']}").json()
        assert body["code"] == s["code"]
        assert c.get(f"/api/stations/{s['code']}/draft").status_code == 200


def test_unknown_station_404(ctx):
    c, *_ = ctx
    assert c.get("/api/stations/NOPE").status_code == 404


def test_ticket_blocked_inside_window_then_forced(ctx):
    c, *_ = ctx
    r = c.post("/api/stations/HLMT/tickets", json={"summary": "HLMT - Station Offline"})
    assert r.status_code == 409 and "Monitoring" in r.json()["detail"]
    r = c.post("/api/stations/HLMT/tickets", json={"summary": "HLMT - Station Offline", "force": True})
    assert r.status_code == 201
    assert c.get("/api/stations/HLMT").json()["decision"]["kind"] == "comment"


def test_cannot_duplicate_open_ticket(ctx):
    c, *_ = ctx
    r = c.post("/api/stations/MRVL/tickets", json={"summary": "MRVL - Station Offline"})
    assert r.status_code == 409 and "NOC-2052" in r.json()["detail"]
    assert c.post("/api/stations/MRVL/comments", json={"body": "Rebooted receivers"}).status_code == 201


def test_slack_inquiry_goes_to_station_owner(ctx):
    c, _, notifier = ctx
    r = c.post("/api/stations/MRVL/inquiries").json()
    assert r["slack"] == "elena.ruiz"
    assert "MRVL" in notifier.sent[0].message
    steps = c.get("/api/stations/MRVL").json()["workflow"]["steps"]
    assert next(s for s in steps if s["id"] == "notify")["state"] == "done"


def test_progress_persists(ctx):
    c, *_ = ctx
    c.post("/api/stations/HLMT/progress", json={"step": "chan", "done": True})
    c.post("/api/stations/HLMT/progress", json={"step": "cp", "value": "CP-12345"})
    steps = {s["id"]: s for s in c.get("/api/stations/HLMT").json()["workflow"]["steps"]}
    assert steps["chan"]["state"] == "done"
    assert steps["cp"]["value"] == "CP-12345"


def test_metrics_exposition(ctx):
    c, *_ = ctx
    text = c.get("/metrics").text
    assert 'noc_stations{status="offline"}' in text and "noc_open_tickets" in text


def test_simulator_changes_state():
    store = StationStore()
    before = {s.code: s.since for s in store.all()}
    ev = tick(store, random.Random(1), utcnow())
    assert ev and ev["event"] in {"fail", "recover"}
    assert any(s.since != before[s.code] for s in store.all())
