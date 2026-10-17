"""Domain model. Shapes loosely follow what the NetCloud, Grafana, SiteVault
and Jira APIs return, flattened into one station record."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Receiver:
    id: str            # "00", "01"
    obs: bool          # real satellite observations reaching the platform
    bytes: bool        # receiver answers on the network
    sats: int
    temp_c: float = 41.0


@dataclass
class Router:
    name: str
    model: str = "IBR900-600M"
    state: str = "online"          # online | offline (as seen by NetCloud)
    wan: str = "Ethernet"          # Ethernet | LTE | LTE (failover) | —
    carrier: str | None = None
    rsrp: int | None = None        # dBm
    sinr: int | None = None        # dB
    last_checkin: datetime | None = None   # None = checking in now
    firmware: str = "7.24.40"
    lan_prefix: str = "192.168.10"


@dataclass
class SiteRecord:
    id: str
    host: str
    power: str
    access: str


@dataclass
class Ticket:
    key: str
    status: str        # Open | In Progress | Escalated to Partner | Completed | Cancelled
    summary: str
    assignee: str | None
    created: datetime
    comments: list[dict] = field(default_factory=list)


@dataclass
class Inquiry:
    to: str
    slack: str
    sent_at: datetime
    message: str


@dataclass
class Station:
    code: str
    region: str
    country: str
    owner_group: str
    router: Router
    receivers: list[Receiver]
    site: SiteRecord
    since: datetime | None = None          # last good observation / start of the problem
    tickets: list[Ticket] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)   # NetCloud log alerts
    solar: bool = False
    ip_mismatch: bool = False
    tunnels_up: int = 4
    heat_events: int = 0
    heat_days: str = ""
    progress: dict[str, object] = field(default_factory=dict)   # manual checklist state
    inquiries: list[Inquiry] = field(default_factory=list)
