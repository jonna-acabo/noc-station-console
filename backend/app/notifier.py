"""Slack inquiries to the station owner.

`MockSlackNotifier` only records messages, which is what the public demo uses.
`SlackWebNotifier` shows how the real integration would post via chat.postMessage.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from typing import Protocol

from .config import OWNERS
from .models import Inquiry, Station
from .workflow import active_ticket, classify, fmt_hours, fmt_when, hours_down


def build_message(s: Station, now: datetime) -> str:
    cond, t, r = classify(s), active_ticket(s), s.router
    o = OWNERS[s.owner_group]
    since = fmt_when(s.since, now) if s.since else "now"
    rx = ", ".join(f"{x.id} Obs {'OK' if x.obs else 'down'} / Bytes {'OK' if x.bytes else 'down'}" for x in s.receivers)
    ticket = f"{t.key} ({t.status})" if t else "none yet"
    return (f"Hi @{o['slack']}, can you check {s.code} ({s.country})? {cond['label']} since {since} "
            f"({fmt_hours(hours_down(s, now))}).\n"
            f"• Router {r.name}: {r.state}, WAN {r.wan}\n"
            f"• Receivers: {rx}\n"
            f"• Ticket: {ticket}\n"
            "Do you know what's happening on site (power, maintenance, partner work)? Thanks!")


class Notifier(Protocol):
    def send(self, s: Station, now: datetime) -> Inquiry: ...


class MockSlackNotifier:
    def __init__(self) -> None:
        self.sent: list[Inquiry] = []

    def send(self, s: Station, now: datetime) -> Inquiry:
        o = OWNERS[s.owner_group]
        inq = Inquiry(to=o["name"], slack=o["slack"], sent_at=now, message=build_message(s, now))
        self.sent.append(inq)
        return inq


class SlackWebNotifier:  # pragma: no cover - needs a real workspace
    """Posts a DM through a Slack bot. Needs SLACK_BOT_TOKEN and owner Slack user IDs."""

    def __init__(self, token: str | None = None, user_ids: dict[str, str] | None = None):
        self.token = token or os.environ["SLACK_BOT_TOKEN"]
        self.user_ids = user_ids or {}

    def send(self, s: Station, now: datetime) -> Inquiry:
        o = OWNERS[s.owner_group]
        text = build_message(s, now)
        body = json.dumps({"channel": self.user_ids[o["slack"]], "text": text}).encode()
        req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if not json.load(resp).get("ok"):
                raise RuntimeError("Slack rejected the message")
        return Inquiry(to=o["name"], slack=o["slack"], sent_at=now, message=text)
