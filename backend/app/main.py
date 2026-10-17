"""NOC Station Console API.

Run locally:  uvicorn app.main:app --reload   (from backend/)
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import workflow as wf
from .config import OWNERS, REGIONS, settings
from .models import Station
from .notifier import MockSlackNotifier, Notifier
from .simulator import tick
from .store import StationStore, utcnow

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


# ---------------------------------------------------------------- request bodies
class TicketIn(BaseModel):
    summary: str = Field(min_length=5, max_length=200)
    description: str = ""
    source: str = "sitevault"     # sitevault | jira
    force: bool = False           # create inside the monitoring window

class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)

class ProgressIn(BaseModel):
    step: str = Field(min_length=1, max_length=40)
    done: bool | None = None
    value: str | None = Field(default=None, max_length=200)


# ---------------------------------------------------------------- views
def summary_view(s: Station, now: datetime) -> dict:
    cond = wf.classify(s)
    t = wf.active_ticket(s)
    d = wf.decision(s, cond, now)
    return {
        "code": s.code, "region": s.region, "country": s.country,
        "status": wf.status_of(s, cond), "condition": cond,
        "since": s.since.isoformat() if s.since else None,
        "hours_down": round(wf.hours_down(s, now), 2),
        "router_name": s.router.name,
        "active_ticket": {"key": t.key, "status": t.status} if t else None,
        "ticket_keys": [x.key for x in s.tickets],
        "decision": d,
    }


def detail_view(s: Station, now: datetime) -> dict:
    cond = wf.classify(s)
    flow = wf.build_workflow(s, cond, now)
    if flow:
        flow = dict(flow)
        flow["steps"] = wf.resolve_steps(flow["steps"], s.progress)
        if flow.get("net"):
            flow["net"] = wf.resolve_steps(flow["net"], s.progress)
            flow["net_counts"] = wf.counts(flow["net"])
        flow["counts"] = wf.counts(flow["steps"] + flow.get("net", []))
    out = summary_view(s, now)
    out.update({
        "region_name": REGIONS[s.region],
        "owner": OWNERS[s.owner_group],
        "solar": s.solar,
        "router": asdict(s.router),
        "receivers": [asdict(x) | {"name": wf.rx_name(s, x.id)} for x in s.receivers],
        "site": asdict(s.site),
        "alerts": s.alerts,
        "tickets": [asdict(t) for t in s.tickets],
        "inquiries": [asdict(i) for i in s.inquiries],
        "hold": wf.hold_of(s, cond, now),
        "workflow": flow,
    })
    return out


# ---------------------------------------------------------------- app
def create_app(store: StationStore | None = None, notifier: Notifier | None = None,
               simulate: bool | None = None) -> FastAPI:
    store = store or StationStore()
    notifier = notifier or MockSlackNotifier()
    simulate = settings.sim_enabled if simulate is None else simulate

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if simulate:
            rng = random.Random(settings.sim_seed)

            async def loop():
                while True:
                    await asyncio.sleep(settings.sim_interval_seconds)
                    tick(store, rng, utcnow())

            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="NOC Station Console", version="1.0.0", lifespan=lifespan)
    app.state.store, app.state.notifier = store, notifier

    def get_station(code: str) -> Station:
        s = store.get(code)
        if not s:
            raise HTTPException(404, f"Station {code} not found")
        return s

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "mock", "simulator": simulate}

    @app.get("/api/stations")
    def list_stations():
        now = utcnow()
        return [summary_view(s, now) for s in store.all()]

    @app.get("/api/stations/{code}")
    def station(code: str):
        return detail_view(get_station(code), utcnow())

    @app.get("/api/alerts")
    def alerts():
        """Problems without an open ticket: the queue a NOC analyst works from."""
        now = utcnow()
        rows = [summary_view(s, now) for s in store.all()]
        rows = [r for r in rows if r["status"] != "ok" and not r["active_ticket"]]
        return sorted(rows, key=lambda r: r["since"] or "", reverse=True)

    @app.get("/api/stations/{code}/draft")
    def draft(code: str, mode: str = "new"):
        s, now = get_station(code), utcnow()
        cond, r, t = wf.classify(s), s.router, wf.active_ticket(s)
        o = OWNERS[s.owner_group]
        kind = ("Station Offline" if wf.status_of(s, cond) == "offline"
                else "DMVPN Issue" if cond["kind"] == "dmvpn" else cond["label"])
        since = s.since or now
        rx = "\n".join(f"  - {wf.rx_name(s, x.id)}: Obs {'OK' if x.obs else 'DOWN'}, Bytes {'OK' if x.bytes else 'DOWN'}, "
                       f"{x.temp_c:g} °C" for x in s.receivers)
        body = (f"Station: {s.code} ({REGIONS[s.region]}, {s.country})\n"
                f"Down since: {since:%Y-%m-%d %H:%M} UTC ({wf.fmt_hours(wf.hours_down(s, now))})\n"
                f"Condition: {cond['label']}\n\nReceivers (Grafana):\n{rx}\n"
                f"Router (NetCloud): {r.name} {r.model}, {r.state}, WAN {r.wan}"
                + (f", RSRP {r.rsrp} dBm" if r.rsrp is not None else "") + "\n"
                f"Site (SiteVault {s.site.id}): {s.site.host}, power {s.site.power}, access {s.site.access}\n"
                f"Station owner: {o['name']} (@{o['slack']})\n\n{wf.findings_text(s, now)}")
        if mode == "comment" and t:
            body = (f"@{t.assignee} " if t.assignee else "") + f"Update from NOC, {now:%H:%M} UTC\n\n" + body
        return {"summary": f"{s.code} - {kind} ({since:%m/%d/%Y})", "description": body,
                "ticket": t.key if t else None}

    @app.post("/api/stations/{code}/tickets", status_code=201)
    def create_ticket(code: str, body: TicketIn):
        s, now = get_station(code), utcnow()
        cond = wf.classify(s)
        d = wf.decision(s, cond, now)
        if d is None:
            raise HTTPException(409, "Station is healthy; nothing to ticket.")
        if d["kind"] == "comment":
            raise HTTPException(409, f"{d['ticket']} is already open. Add a comment instead.")
        if d["kind"] == "wait" and not body.force:
            raise HTTPException(409, d["text"])
        t = store.create_ticket(s, body.summary, now)
        return {"key": t.key, "status": t.status, "source": body.source}

    @app.post("/api/stations/{code}/comments", status_code=201)
    def add_comment(code: str, body: CommentIn):
        s = get_station(code)
        t = store.add_comment(s, body.body, utcnow())
        if not t:
            raise HTTPException(409, "No open ticket to comment on.")
        return {"ticket": t.key, "comments": len(t.comments)}

    @app.post("/api/stations/{code}/progress")
    def progress(code: str, body: ProgressIn):
        s = get_station(code)
        if body.done is not None:
            store.set_progress(s, body.step, body.done)
        if body.value is not None:
            store.set_progress(s, f"in_{body.step}", body.value)
        return {"ok": True}

    @app.post("/api/stations/{code}/inquiries", status_code=201)
    def inquire(code: str):
        s, now = get_station(code), utcnow()
        inq = notifier.send(s, now)
        store.add_inquiry(s, inq, now)
        return asdict(inq)

    @app.post("/api/simulate/tick")
    def simulate_tick():
        return tick(store, random.Random(), utcnow()) or {"event": "none"}

    @app.get("/api/events")
    def events():
        return store.events

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        """Prometheus exposition format, so Grafana can chart the console itself."""
        rows = [summary_view(s, utcnow()) for s in store.all()]
        lines = ["# HELP noc_stations Stations by status", "# TYPE noc_stations gauge"]
        for st in ("ok", "degraded", "offline"):
            lines.append(f'noc_stations{{status="{st}"}} {sum(r["status"] == st for r in rows)}')
        lines += ["# HELP noc_alerts_unticketed Problems without an open ticket", "# TYPE noc_alerts_unticketed gauge",
                  f"noc_alerts_unticketed {sum(r['status'] != 'ok' and not r['active_ticket'] for r in rows)}",
                  "# HELP noc_open_tickets Open tickets", "# TYPE noc_open_tickets gauge",
                  f"noc_open_tickets {sum(bool(r['active_ticket']) for r in rows)}"]
        return "\n".join(lines) + "\n"

    if FRONTEND.exists():
        app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    return app


app = create_app()
