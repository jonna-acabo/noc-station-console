"""Station support workflow engine.

Given one station's combined state (router + receivers + site + tickets), it:
  1. classifies the condition (which section of the support runbook applies),
  2. builds the runbook checklist, auto-completing every step the data can answer,
  3. applies the ticket rules (wait / comment on an open ticket / create a new one).

Pure functions of (station, now) so they are easy to unit test.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import BACKUP_CONTACT, OWNERS, settings
from .models import Station, Ticket

ACTIVE_STATUSES = {"Open", "In Progress", "Escalated to Partner", "Stable", "Postmortem"}
DMVPN_ALERT = "IPSec VPN Tunnel Down"


# ---------------------------------------------------------------- helpers
def hours_down(s: Station, now: datetime) -> float:
    return max(0.0, (now - s.since).total_seconds() / 3600) if s.since else 0.0


def fmt_hours(h: float) -> str:
    m = round(max(0.0, h) * 60)
    if m < 60:
        return f"{m}m"
    if m >= 2880:
        return f"{round(m / 1440)}d"
    return f"{m // 60}h" + (f" {m % 60}m" if m % 60 else "")


def fmt_when(dt: datetime, now: datetime) -> str:
    """'14:00 UTC' today, '02:00 UTC Oct 18' on another day."""
    s = dt.strftime("%H:%M UTC")
    return s if dt.date() == now.date() else f"{s} {dt.strftime('%b')} {dt.day}"


def active_ticket(s: Station) -> Ticket | None:
    return next((t for t in s.tickets if t.status in ACTIVE_STATUSES), None)


def closed_ticket(s: Station) -> Ticket | None:
    return next((t for t in s.tickets if t.status not in ACTIVE_STATUSES), None)


def rx_name(s: Station, rx_id: str) -> str:
    return f"{s.code}{rx_id}{s.country}"


# ---------------------------------------------------------------- 1. classify
def classify(s: Station) -> dict:
    r, rx = s.router, s.receivers
    obs_down = [x for x in rx if not x.obs]
    bytes_down = [x for x in rx if not x.bytes]

    if DMVPN_ALERT in s.alerts:
        return {"kind": "dmvpn", "label": "IPSec VPN tunnel down",
                "hint": "NetCloud logged a DMVPN tunnel drop. Obs are still flowing, so work the DMVPN "
                        "checks before it becomes an outage."}
    if r.state == "offline" and any(x.obs for x in rx):
        return {"kind": "mismatch", "label": "NetCloud offline, platform still receiving",
                "hint": "Grafana shows obs flowing, so the station itself is fine. The router is having "
                        "trouble reaching NetCloud."}
    if r.state == "offline":
        hint = ("Solar site: it often recovers on its own once the sun is up. Monitor before ticketing."
                if s.solar else
                "Router and receivers are unreachable. Monitor while the remote power relay tries a reboot; the ticket "
                f"unlocks automatically once Grafana shows {settings.ticket_after_hours:g} hours down.")
        return {"kind": "router", "label": "Router offline", "hint": hint}
    if obs_down:
        if len(bytes_down) == len(rx):
            return {"kind": "obs", "sub": "router", "label": "Obs and Bytes down on all receivers",
                    "hint": "Router is online but nothing arrives from either receiver. Usually a router-level, "
                            "station hardware or DMVPN config issue."}
        if bytes_down:
            ids = ", ".join(x.id for x in bytes_down)
            return {"kind": "obs", "sub": "unreachable", "label": f"Receiver {ids} unreachable",
                    "hint": "The router can't reach this receiver. Start with the client list and firewall settings."}
        ids = ", ".join(x.id for x in obs_down)
        return {"kind": "obs", "sub": "tracking", "label": f"Receiver {ids} not tracking",
                "hint": "Bytes are steady, so the receiver has power and network but isn't getting satellites. "
                        "Check its config, then reboot it via the remote power relay. If it doesn't recover, suspect the "
                        "antenna, cable or port."}
    if any(x.temp_c >= settings.heat_alert_celsius for x in rx):
        return {"kind": "heat", "label": "Receiver overheating",
                "hint": f"{s.heat_events} heat alerts in the last 14 days. Check the weather and monitor for "
                        f"{settings.heat_monitor_hours:g} hours."}
    if "failover" in r.wan:
        return {"kind": "net", "label": "Primary WAN down, on LTE",
                "hint": "Corrections are still flowing. Work the NetCloud checklist and raise it with the site's ISP."}
    if r.rsrp is not None and r.rsrp < settings.weak_rsrp_dbm:
        return {"kind": "net", "label": "Weak cellular signal",
                "hint": f"RSRP is below {settings.weak_rsrp_dbm:g} dBm. Expect intermittent drops."}
    return {"kind": "ok", "label": "Healthy", "hint": "No action needed."}


def status_of(s: Station, cond: dict | None = None) -> str:
    cond = cond or classify(s)
    if cond["kind"] == "ok":
        return "ok"
    if cond["kind"] == "router" or (cond["kind"] == "obs" and all(not x.obs for x in s.receivers)):
        return "offline"
    return "degraded"


# ---------------------------------------------------------------- 2. hold / gate
def hold_of(s: Station, cond: dict, now: datetime) -> dict | None:
    """Monitoring window before a ticket is allowed (None = no window)."""
    if cond["kind"] in {"router", "obs"}:
        hours, why = settings.ticket_after_hours, "Grafana down-time check"
    elif cond["kind"] == "heat":
        hours, why = settings.heat_monitor_hours, "heat monitoring window"
    else:
        return None
    el = hours_down(s, now)
    unlock = s.since + timedelta(hours=hours)
    return {"hours": hours, "elapsed": round(el, 2), "unlock_at": unlock.isoformat(),
            "unlock_label": fmt_when(unlock, now), "since_label": fmt_when(s.since, now),
            "done": el >= hours, "why": why}


def grafana_check(s: Station, now: datetime) -> dict:
    el, h = hours_down(s, now), settings.ticket_after_hours
    last = fmt_when(s.since, now)
    if el >= h:
        auto = {"state": "done", "note": f"Last obs {last} · down {fmt_hours(el)} · ticket allowed"}
    else:
        unlock = fmt_when(s.since + timedelta(hours=h), now)
        auto = {"state": "wait",
                "note": f"Last obs {last} · down {fmt_hours(el)} · ticket allowed at {unlock} (in {fmt_hours(h - el)})"}
    return {"id": "g24", "title": f"Grafana check: down for {h:g} hours?",
            "detail": "Checked automatically from the last observation in Grafana.", "timer": True, "auto": auto}


# ---------------------------------------------------------------- 3. checklists
def network_checks(s: Station) -> list[dict]:
    r = s.router
    hottest = max(x.temp_c for x in s.receivers)
    power = next((a for a in s.alerts if "Battery" in a), None)
    return [
        {"id": "cell", "title": "Check cellular health",
         "auto": {"state": "skip", "note": "Ethernet WAN, no cellular"} if r.rsrp is None else
         {"state": "fail" if r.rsrp < settings.weak_rsrp_dbm else "done", "note": f"RSRP {r.rsrp} dBm · SINR {r.sinr} dB"}},
        {"id": "speed", "title": "Run a NetCloud speed test",
         "detail": "Upload should not be above 1 Mbps, and latency should be low."},
        {"id": "dip", "title": "Check the network connectivity panel in Grafana",
         "detail": "A dip that lasts a long time points to a network connectivity issue."},
        {"id": "wan", "title": "Check WAN status",
         "auto": {"state": "fail" if ("failover" in r.wan or r.wan == "—") else "done", "note": f"Active WAN: {r.wan}"}},
        {"id": "power", "title": "Is it power related?",
         "detail": "Power outage: ticket the MNO. Owned site: contact the site owner. No power alerts: likely "
                   "communications, may need a site visit.",
         "auto": {"state": "fail", "note": power + (" · usually recovers on its own; follow up with the landlord"
                                                    if "exhausted" in power else "")}
         if power else {"state": "done", "note": "No power or battery alerts"}},
        {"id": "ip", "title": "Router and receiver IPs match in SiteVault and NetCloud",
         "auto": {"state": "fail", "note": "Mismatch on receiver 01 LAN IP · ask Network Ops to correct SiteVault"}
         if s.ip_mismatch else {"state": "done", "note": "IPs match"}},
        {"id": "fw", "title": "Port forward / firewall settings correct",
         "detail": "If wrong, fix them together with the network team."},
        {"id": "coords", "title": "Antenna coordinates match SiteVault"},
        {"id": "temp", "title": "Check weather and receiver temperature",
         "auto": {"state": "fail" if hottest >= settings.heat_alert_celsius else "done",
                  "note": f"Hottest receiver {hottest:g} °C"}},
        {"id": "eth", "title": "Check Ethernet ports, VTI set to ANY, and Starlink DHCP errors",
         "detail": '"DHCP Client Reported Failure" in Connection Manager means the Starlink needs a reboot.'},
        {"id": "creds", "title": "Everything OK? Ask the platform team to check for bad login credentials"},
    ]


def _ticket_step(s: Station, cond: dict, now: datetime) -> dict:
    t, hold = active_ticket(s), hold_of(s, cond, now)
    if t:
        auto = {"state": "done", "note": f"{t.key} is {t.status}"}
    elif hold and not hold["done"]:
        auto = {"state": "lock", "note": f"Unlocks {hold['unlock_label']}"}
    else:
        auto = None
    return {"id": "ticket", "title": "Create the ticket from SiteVault (SLA trigger)",
            "detail": "This creates the Jira ticket. If SiteVault is down, create it in Jira and note that no "
                      "SiteVault ticket exists.", "auto": auto}


def _notify_step(s: Station) -> dict:
    o = OWNERS[s.owner_group]
    asked = bool(s.inquiries)
    return {"id": "notify", "title": f"Notify {o['name']} ({o['role']})",
            "detail": f"{o.get('note', '')} If they're away, use their designated backup or the {BACKUP_CONTACT}.".strip(),
            "ask": True,
            "auto": {"state": "done", "note": f"Slack inquiry sent to @{o['slack']}"} if asked else None}


def build_workflow(s: Station, cond: dict, now: datetime) -> dict | None:
    r, kind = s.router, cond["kind"]
    el = hours_down(s, now)
    o = OWNERS[s.owner_group]

    if kind == "router":
        steps = [
            {"id": "logs", "title": "Check NetCloud activity and device logs",
             "detail": "Find the alert type before anything else.",
             "auto": {"state": "done", "note": f"{(s.alerts or ['Router check-in missed'])[0]} · last check-in "
                                               f"{fmt_when(r.last_checkin, now) if r.last_checkin else 'now'}"}},
            {"id": "chan", "title": "Check the deployments Slack channel",
             "detail": "Look for planned work or a known outage at this site."},
            {"id": "both", "title": "Confirm the state in both NetCloud and Grafana",
             "detail": "NetCloud can show offline while the platform still gets data.",
             "auto": {"state": "done", "note": "NetCloud offline · Grafana no Obs, no Bytes · real outage"}},
            {"id": "solar", "title": "Solar site: check for daylight",
             "detail": "Solar stations often come back once the sun is up.",
             "auto": {"state": "wait", "note": "Solar + battery site"}} if s.solar else None,
            {"id": "relay", "title": f"Monitor {settings.relay_reboot_hours:g} hours for the remote power relay to reboot the router",
             "detail": "Don't create a ticket yet.",
             "auto": {"state": "done", "note": f"Offline {fmt_hours(el)} · reboot window passed, still down"}
             if el >= settings.relay_reboot_hours else
             {"state": "wait", "note": f"{fmt_hours(settings.relay_reboot_hours - el)} left in the reboot window"}},
            grafana_check(s, now),
            _ticket_step(s, cond, now),
            _notify_step(s),
            {"id": "sla", "title": f"Partner SLA: at {o['sla_hours']} hours, email the partner",
             "detail": "Follow the partner's outage reporting procedure.",
             "auto": {"state": "fail", "note": f"Offline {fmt_hours(el)} · partner email due"}
             if el >= o["sla_hours"] else
             {"state": "wait", "note": f"Due at {fmt_when(s.since + timedelta(hours=o['sla_hours']), now)}"}}
            if o.get("sla_hours") else None,
            {"id": "cp", "title": "File a Cradlepoint support ticket",
             "detail": "Ask them to review the router logs. Add the CP reference to the ticket.",
             "input": "CP ticket reference"},
        ]
        return {"title": "Router is offline", "steps": [x for x in steps if x]}

    if kind == "obs":
        unreach = [x for x in s.receivers if not x.bytes]
        n = len(s.receivers) + 1
        steps = [
            {"id": "clients", "title": "NetCloud client list: all components listed",
             "detail": "Ping each component from NetCloud.",
             "auto": {"state": "fail" if unreach else "done",
                      "note": f"{n - len(unreach)} of {n} clients listed (router, "
                              + ", ".join(f"Rx {x.id}" for x in s.receivers) + ")"}},
            {"id": "reach", "title": "Are the receivers reachable?",
             "auto": {"state": "fail" if unreach else "done",
                      "note": " · ".join(f"{x.id} {'reachable' if x.bytes else 'unreachable'}" for x in s.receivers)}},
            {"id": "fwl", "title": "Unreachable receiver: check firewall settings"} if unreach else None,
            {"id": "group", "title": "All receivers down and not DMVPN: check the NetCloud group"}
            if len(unreach) == len(s.receivers) else None,
            {"id": "cfg", "title": "Reachable receiver: check its configuration",
             "detail": "Open the receiver's web interface and confirm the settings."}
            if any(x.bytes and not x.obs for x in s.receivers) else None,
            {"id": "ping", "title": "Ping the receiver via NetCloud"},
            {"id": "reboot", "title": "Reboot the receiver via the remote power relay",
             "detail": "Check the dashboard afterwards. If it doesn't come back, suspect the cable or port."},
            grafana_check(s, now),
            _ticket_step(s, cond, now),
            _notify_step(s),
        ]
        return {"title": "Router online, Obs down", "steps": [x for x in steps if x], "net": network_checks(s)}

    if kind == "dmvpn":
        ip = r.lan_prefix
        return {"title": "DMVPN: IPSec VPN tunnel down", "steps": [
            {"id": "logs", "title": "Alert seen in NetCloud logs",
             "auto": {"state": "done", "note": f"{DMVPN_ALERT} · {fmt_when(s.since, now)}"}},
            {"id": "pingnc", "title": "Ping the router via NetCloud"},
            {"id": "pingterm", "title": "Ping the router from a terminal",
             "detail": "Sign in to the VPN first. You should see packets come back.",
             "code": f"ping 10.40.{ord(s.code[0])}.1"},
            {"id": "rxip", "title": "Open each receiver by its LAN IP from SiteVault assets",
             "auto": {"state": "info", "note": " · ".join(f"Rx {x.id}: {ip}.{11 + int(x.id)}" for x in s.receivers)}},
            {"id": "web", "title": "Verify the receiver's NTRIP web server",
             "detail": "The first command lists the source table. The second checks that the receiver login works.",
             "code": f"curl --http0.9 -v {ip}.11:2101\ncurl --http0.9 -v -u <user>:<password> {ip}.11:2101"},
            {"id": "tunnels", "title": "Verify all 4 tunnels exist (2 primary, 2 secondary)",
             "auto": {"state": "fail" if s.tunnels_up < 4 else "done", "note": f"{s.tunnels_up} of 4 tunnels up"}},
            _ticket_step(s, cond, now),
            _notify_step(s),
        ]}

    if kind == "mismatch":
        return {"title": "NetCloud offline, platform online", "steps": [
            {"id": "obs", "title": "Confirm Obs in Grafana",
             "auto": {"state": "done", "note": " · ".join(f"{rx_name(s, x.id)} tracking {x.sats} sats" for x in s.receivers)}},
            {"id": "both", "title": "Confirm the router state in NetCloud",
             "auto": {"state": "fail", "note": f"Last check-in {fmt_when(r.last_checkin, now) if r.last_checkin else 'unknown'}"}},
            {"id": "cell", "title": "Check the router's cellular health and WAN",
             "auto": {"state": "fail" if r.rsrp < settings.weak_rsrp_dbm else "done",
                      "note": f"RSRP {r.rsrp} dBm · SINR {r.sinr} dB · probably why NetCloud lost it"}
             if r.rsrp is not None else {"state": "done", "note": r.wan}},
            {"id": "watch", "title": "Keep monitoring the NetCloud check-in",
             "detail": f"Station data is fine, so there's no station ticket. If it doesn't check in again, raise it "
                       f"with the {BACKUP_CONTACT}."},
        ]}

    if kind == "heat":
        recurring = s.heat_events >= settings.heat_recurring_events
        return {"title": "Temperature / overheating", "steps": [
            {"id": "logs", "title": "Check alerts and system logs for a pattern",
             "auto": {"state": "info", "note": f"{s.heat_events} heat alerts in 14 days: {s.heat_days}"}},
            {"id": "wx", "title": "Check the weather in the area"},
            {"id": "temp", "title": "Check receiver temperature",
             "auto": {"state": "fail", "note": " · ".join(f"Rx {x.id} {x.temp_c:g} °C" for x in s.receivers)}},
            {"id": "hold", "title": f"Monitor for {settings.heat_monitor_hours:g} hours", "timer": True,
             "auto": {"state": "done", "note": "Monitoring window complete"}
             if el >= settings.heat_monitor_hours else
             {"state": "wait", "note": f"{fmt_hours(settings.heat_monitor_hours - el)} left"}},
            {"id": "rec", "title": "Recurring? (3 days in a row, or on different days over 2 weeks in a month)",
             "auto": {"state": "fail", "note": "Yes, recurring · create a ticket and notify the network team"}
             if recurring else {"state": "done", "note": "Not recurring"}},
            _ticket_step(s, cond, now),
        ]}

    if kind == "net":
        return {"title": "NetCloud checklist", "steps": [_ticket_step(s, cond, now), _notify_step(s)],
                "net": network_checks(s), "net_open": True}
    return None


# ---------------------------------------------------------------- 4. ticket rule
def decision(s: Station, cond: dict, now: datetime) -> dict | None:
    if cond["kind"] == "ok":
        return None
    t, old, hold = active_ticket(s), closed_ticket(s), hold_of(s, cond, now)
    if t:
        return {"kind": "comment", "ticket": t.key,
                "text": f"{t.key} is already {t.status} for this station. Add your new findings as a comment and "
                        f"tag {t.assignee or 'the owner'} instead of opening a new ticket.",
                "button": f"Add comment to {t.key}"}
    if cond["kind"] == "mismatch":
        return {"kind": "none", "text": "No ticket needed. The platform is getting data, so this is a NetCloud "
                                        "connection issue rather than a station outage."}
    if cond["kind"] == "heat" and s.heat_events < settings.heat_recurring_events:
        return {"kind": "wait", "text": "Not recurring yet. Monitor, and create a ticket only if it keeps happening."}
    if hold and not hold["done"]:
        return {"kind": "wait", "unlock_label": hold["unlock_label"],
                "text": f"Monitoring. Grafana shows {fmt_hours(hold['elapsed'])} down. If it's still down at "
                        f"{hold['unlock_label']} ({hold['hours']:g}h), the ticket unlocks automatically.",
                "button": "Create anyway"}
    prev = f"No open ticket. {old.key} is {old.status.lower()}, so open a new one." if old else "No related ticket. Create one."
    return {"kind": "create", "text": prev, "button": "Create ticket"}


# ---------------------------------------------------------------- 5. resolve progress
def resolve_steps(steps: list[dict], progress: dict) -> list[dict]:
    """Attach a final `state` to every step: auto steps keep theirs, manual steps read saved progress."""
    out = []
    for st in steps:
        st = dict(st)
        st["manual"] = st.get("auto") is None
        st["state"] = st["auto"]["state"] if st.get("auto") else ("done" if progress.get(st["id"]) else "todo")
        if st.get("input"):
            st["value"] = progress.get(f"in_{st['id']}", "")
        out.append(st)
    return out


def counts(steps: list[dict]) -> tuple[int, int]:
    relevant = [x for x in steps if x["state"] not in {"skip", "info"}]
    return sum(x["state"] == "done" for x in relevant), len(relevant)


def findings_text(s: Station, now: datetime) -> str:
    cond = classify(s)
    wf = build_workflow(s, cond, now)
    if not wf:
        return ""
    marks = {"done": "[x]", "fail": "[!]", "wait": "[~]", "lock": "[~]", "skip": "[-]", "info": "[i]", "todo": "[ ]"}
    lines = []
    for st in resolve_steps(wf["steps"] + wf.get("net", []), s.progress):
        if st["id"] == "ticket":
            continue
        line = f"{marks[st['state']]} {st['title']}"
        if st.get("auto"):
            line += f" — {st['auto']['note']}"
        if st.get("value"):
            line += f" — {st['value']}"
        lines.append(line)
    return f"Workflow: {wf['title']}\n" + "\n".join(lines)
