"""Runtime settings and the station-owner routing table.

Everything here can be overridden with environment variables so the same image
runs in demo mode locally, in CI and on a public host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Support-workflow thresholds
    ticket_after_hours: float = _env_float("TICKET_AFTER_HOURS", 24)
    relay_reboot_hours: float = _env_float("RELAY_REBOOT_HOURS", 12)
    heat_monitor_hours: float = _env_float("HEAT_MONITOR_HOURS", 8)
    heat_alert_celsius: float = _env_float("HEAT_ALERT_CELSIUS", 70)
    heat_recurring_events: int = int(_env_float("HEAT_RECURRING_EVENTS", 3))
    weak_rsrp_dbm: float = _env_float("WEAK_RSRP_DBM", -105)

    # Demo simulator
    sim_enabled: bool = _env_bool("SIM_ENABLED", True)
    sim_interval_seconds: float = _env_float("SIM_INTERVAL_SECONDS", 90)
    sim_seed: int = int(_env_float("SIM_SEED", 7))


settings = Settings()

# Who owns which stations. In production this would come from a config file or
# the HR/on-call system, with real Slack user IDs. All names here are fictional.
OWNERS: dict[str, dict] = {
    "conus": {"name": "Marco Lopez", "role": "US station owner", "slack": "marco.lopez",
              "note": "They contact the property manager."},
    "canada": {"name": "Kim Park", "role": "Canada station owner", "slack": "kim.park"},
    "apac24": {"name": "Jun Tan", "role": "APAC station owner", "slack": "jun.tan", "sla_hours": 24,
               "note": "This partner has a 24-hour outage SLA."},
    "apac": {"name": "Jun Tan", "role": "APAC station owner", "slack": "jun.tan"},
    "eu": {"name": "Elena Ruiz", "role": "EU station owner", "slack": "elena.ruiz"},
}
BACKUP_CONTACT = "network team lead"
REGIONS = {"na": "North America", "eu": "Europe", "ap": "Asia-Pacific"}
