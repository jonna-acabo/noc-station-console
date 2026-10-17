"""Demo simulator: every tick, one healthy station may fail or one failing
station (without an open ticket) may recover, so the live demo keeps moving."""

from __future__ import annotations

import random
from datetime import datetime

from .models import Station
from .store import StationStore
from .workflow import active_ticket, classify

FAILURES = ("router_offline", "rx_not_tracking", "lte_failover", "dmvpn")


def break_station(s: Station, kind: str, now: datetime) -> str:
    s.since = now
    if kind == "router_offline":
        s.router.state, s.router.wan, s.router.last_checkin = "offline", "—", now
        for x in s.receivers:
            x.obs = x.bytes = False
            x.sats = 0
        s.alerts = ["Router check-in missed"]
        return "Router went offline"
    if kind == "rx_not_tracking":
        x = s.receivers[-1]
        x.obs, x.sats = False, 0
        return f"Receiver {x.id} stopped tracking"
    if kind == "lte_failover":
        s.router.wan, s.router.carrier, s.router.rsrp, s.router.sinr = "LTE (failover)", "Carrier A", -103, 5
        return "Primary WAN down, failed over to LTE"
    s.alerts, s.tunnels_up = ["IPSec VPN Tunnel Down"], 2
    return "IPSec VPN tunnel down"


def tick(store: StationStore, rng: random.Random, now: datetime) -> dict | None:
    stations = store.all()
    healthy = [s for s in stations if classify(s)["kind"] == "ok"]
    # Only recover stations nobody is actively working on (no open ticket, no inquiry).
    recoverable = [s for s in stations if classify(s)["kind"] != "ok" and not active_ticket(s) and not s.inquiries]

    if healthy and (not recoverable or rng.random() < 0.5):
        s = rng.choice(healthy)
        text = break_station(s, rng.choice(FAILURES), now)
        store.log("fail", s.code, text, now)
        return {"station": s.code, "event": "fail", "text": text}
    if recoverable:
        s = rng.choice(recoverable)
        store.restore(s, now)
        return {"station": s.code, "event": "recover", "text": "Station recovered"}
    return None
