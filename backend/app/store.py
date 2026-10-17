"""In-memory station store seeded with mock data.

The seed is built relative to `now`, so whenever the app starts the demo shows
the same mix: some stations still inside the 24-hour window, some past it,
some with open tickets, some healthy. Every name and code is fictional.
"""

from __future__ import annotations

import copy
import threading
from datetime import UTC, datetime, timedelta

from .models import Inquiry, Receiver, Router, SiteRecord, Station, Ticket
from .workflow import active_ticket


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _rx(rid: str, obs: bool, byts: bool, sats: int = 0, temp: float = 41) -> Receiver:
    return Receiver(id=rid, obs=obs, bytes=byts, sats=sats if obs else 0, temp_c=temp)


def seed(now: datetime) -> list[Station]:
    ago = lambda h: now - timedelta(hours=h)  # noqa: E731
    T = lambda key, status, summary, who, h: Ticket(key, status, summary, who, ago(h))  # noqa: E731
    day = lambda h: ago(h).strftime("%m/%d/%Y")  # noqa: E731

    return [
        Station("KSTR", "eu", "DEU", "eu",
                Router("KSTR-RTR01", state="offline", wan="—", last_checkin=ago(122)),
                [_rx("00", False, False), _rx("01", False, False)],
                SiteRecord("SV-10417", "Partner rooftop", "Grid", "Escort required"),
                since=ago(122), alerts=["Router check-in missed"],
                tickets=[T("NOC-2048", "Open", f"KSTR - Station Offline ({day(120)})", "Elena Ruiz", 96),
                         T("NOC-1977", "Completed", "KSTR - Receiver 01 not tracking", "Elena Ruiz", 1300)]),
        Station("HLMT", "ap", "JPN", "apac24",
                Router("HLMT-RTR01", state="offline", wan="—", last_checkin=ago(6.05), firmware="7.24.10"),
                [_rx("00", False, False)],
                SiteRecord("SV-15120", "Partner site", "Grid + UPS", "Partner only"),
                since=ago(6), alerts=["WAN link down"]),
        Station("SAGE", "na", "USA", "conus",
                Router("SAGE-RTR01", state="offline", wan="—", last_checkin=ago(3.55)),
                [_rx("00", False, False), _rx("01", False, False)],
                SiteRecord("SV-02240", "Ranch outbuilding", "Solar + battery", "Key holder"),
                since=ago(3.5), solar=True, alerts=["Battery backup exhausted"],
                tickets=[T("NOC-1958", "Completed", "SAGE - Station Offline", "Marco Lopez", 1800)]),
        Station("RDGE", "na", "USA", "conus",
                Router("RDGE-RTR01", wan="LTE", carrier="Carrier A", rsrp=-97, sinr=9),
                [_rx("00", False, False), _rx("01", False, False)],
                SiteRecord("SV-02210", "Partner rooftop", "Grid", "24/7"),
                since=ago(40), ip_mismatch=True,
                tickets=[T("NOC-2055", "In Progress", f"RDGE - Station Offline ({day(40)})", "Marco Lopez", 15)]),
        Station("PNWD", "eu", "GRC", "eu",
                Router("PNWD-RTR01", model="IBR1700-1200M", wan="LTE", carrier="Carrier B", rsrp=-109, sinr=2,
                       firmware="7.24.30"),
                [_rx("00", False, False)],
                SiteRecord("SV-09981", "Telecom mast", "Grid", "Key holder"),
                since=ago(190), alerts=["Battery backup active"],
                tickets=[T("NOC-2041", "Escalated to Partner", f"PNWD - Station Offline ({day(190)})", "Elena Ruiz", 160)]),
        Station("MRVL", "eu", "ITA", "eu",
                Router("MRVL-RTR01"),
                [_rx("00", False, True), _rx("01", False, True)],
                SiteRecord("SV-10402", "Partner equipment room", "Grid + UPS", "Business hours"),
                since=ago(75),
                tickets=[T("NOC-2052", "Open", f"MRVL - Station Offline ({day(75)})", "Elena Ruiz", 50)]),
        Station("FRST", "na", "CAN", "canada",
                Router("FRST-RTR01"),
                [_rx("00", True, True, 34), _rx("01", False, True)],
                SiteRecord("SV-02018", "Partner rooftop", "Grid", "24/7"),
                since=ago(4.8),
                tickets=[T("NOC-1990", "Completed", "FRST - Station Offline", "Kim Park", 980)]),
        Station("WLLW", "ap", "AUS", "apac",
                Router("WLLW-RTR01"),
                [_rx("00", True, True, 33), _rx("01", False, False)],
                SiteRecord("SV-15031", "Partner equipment room", "Grid + UPS", "Partner only"),
                since=ago(346),
                tickets=[T("NOC-2016", "Escalated to Partner", "WLLW - Receiver 01 not tracking", "Jun Tan", 330)]),
        Station("SNDR", "eu", "ESP", "eu",
                Router("SNDR-RTR01", model="IBR1700-1200M", firmware="7.24.30"),
                [_rx("00", True, True, 31)],
                SiteRecord("SV-09344", "Partner rooftop", "Grid", "24/7"),
                since=ago(6.75), alerts=["IPSec VPN Tunnel Down"], tunnels_up=2,
                tickets=[T("NOC-1931", "Completed", "SNDR - DMVPN Issue", "Elena Ruiz", 2500)]),
        Station("ASPN", "na", "USA", "conus",
                Router("ASPN-RTR01", state="offline", wan="LTE", carrier="Carrier A", rsrp=-111, sinr=1,
                       last_checkin=ago(1.7)),
                [_rx("00", True, True, 36)],
                SiteRecord("SV-02230", "Water tower", "Grid", "Key holder"),
                since=ago(1.67)),
        Station("BRCH", "na", "USA", "conus",
                Router("BRCH-RTR01"),
                [_rx("00", True, True, 33, 74), _rx("01", True, True, 33, 72)],
                SiteRecord("SV-02014", "Partner rooftop", "Grid", "24/7"),
                since=ago(3), heat_events=4, heat_days="4 separate days in the last 2 weeks"),
        Station("QRRY", "eu", "FRA", "eu",
                Router("QRRY-RTR01", wan="LTE", carrier="Carrier C", rsrp=-112, sinr=-1),
                [_rx("00", True, True, 24)],
                SiteRecord("SV-09760", "Telecom mast", "Grid", "Key holder"),
                since=ago(138),
                tickets=[T("NOC-2044", "Open", "QRRY - Receiver Intermittent Connectivity", None, 130)]),
        Station("CPRK", "na", "CAN", "canada",
                Router("CPRK-RTR01", model="IBR1700-1200M", wan="LTE (failover)", carrier="Carrier A", rsrp=-104, sinr=4),
                [_rx("00", True, True, 32), _rx("01", True, True, 31)],
                SiteRecord("SV-02016", "Partner equipment room", "Grid + UPS", "Business hours"),
                since=ago(2.3),
                tickets=[T("NOC-1972", "Completed", "CPRK - DMVPN Issue", "Kim Park", 1150)]),
        Station("OKLN", "na", "CAN", "canada", Router("OKLN-RTR01"),
                [_rx("00", True, True, 35), _rx("01", True, True, 34)],
                SiteRecord("SV-02011", "Partner rooftop", "Grid", "24/7")),
        Station("LKVW", "eu", "DEU", "eu", Router("LKVW-RTR01"),
                [_rx("00", True, True, 34), _rx("01", True, True, 35)],
                SiteRecord("SV-10433", "Partner rooftop", "Grid", "24/7")),
        Station("BAYS", "na", "USA", "conus", Router("BAYS-RTR01", wan="LTE", carrier="Carrier A", rsrp=-88, sinr=16),
                [_rx("00", True, True, 36)],
                SiteRecord("SV-02251", "Water tower", "Grid", "Key holder")),
    ]


class StationStore:
    """Thread-safe in-memory store. Swap for a database in a real deployment."""

    def __init__(self, now: datetime | None = None):
        self._lock = threading.RLock()
        self._stations = {s.code: s for s in seed(now or utcnow())}
        self._healthy = {c: self._healthy_copy(s) for c, s in self._stations.items()}
        self._next_ticket = 2060
        self.events: list[dict] = []   # simulator / action log, newest first

    @staticmethod
    def _healthy_copy(s: Station) -> dict:
        r = copy.deepcopy(s.router)
        r.state, r.last_checkin = "online", None
        if r.wan in {"—", "LTE (failover)"}:
            r.wan = "Ethernet"
        if r.rsrp is not None and r.wan == "Ethernet":
            r.carrier = r.rsrp = r.sinr = None
        if r.rsrp is not None and r.rsrp < -100:
            r.rsrp, r.sinr = -92, 12
        rx = [Receiver(x.id, True, True, 33 + i, 41) for i, x in enumerate(s.receivers)]
        return {"router": r, "receivers": rx}

    # ---- reads
    def all(self) -> list[Station]:
        with self._lock:
            return list(self._stations.values())

    def get(self, code: str) -> Station | None:
        with self._lock:
            return self._stations.get(code.upper())

    def log(self, kind: str, code: str, text: str, now: datetime) -> None:
        self.events.insert(0, {"at": now.isoformat(), "kind": kind, "station": code, "text": text})
        del self.events[50:]

    # ---- writes
    def create_ticket(self, s: Station, summary: str, now: datetime) -> Ticket:
        with self._lock:
            t = Ticket(f"NOC-{self._next_ticket}", "Open", summary, None, now)
            self._next_ticket += 1
            s.tickets.insert(0, t)
            self.log("ticket", s.code, f"{t.key} created", now)
            return t

    def add_comment(self, s: Station, body: str, now: datetime) -> Ticket | None:
        with self._lock:
            t = active_ticket(s)
            if t:
                t.comments.append({"at": now.isoformat(), "body": body})
                self.log("comment", s.code, f"Comment added to {t.key}", now)
            return t

    def set_progress(self, s: Station, key: str, value: object) -> None:
        with self._lock:
            s.progress[key] = value

    def add_inquiry(self, s: Station, inq: Inquiry, now: datetime) -> None:
        with self._lock:
            s.inquiries.insert(0, inq)
            self.log("slack", s.code, f"Inquiry sent to @{inq.slack}", now)

    def restore(self, s: Station, now: datetime) -> None:
        """Bring a station back to healthy (used by the simulator)."""
        with self._lock:
            h = copy.deepcopy(self._healthy[s.code])
            s.router, s.receivers = h["router"], h["receivers"]
            s.since, s.alerts, s.tunnels_up, s.ip_mismatch = None, [], 4, False
            s.progress, s.inquiries = {}, []
            for t in s.tickets:
                if t.status in {"Open", "In Progress", "Escalated to Partner"}:
                    t.status = "Completed"
            self.log("recover", s.code, "Station recovered", now)
