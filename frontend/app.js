// NOC Station Console frontend. All logic lives in the API; this file only renders and posts actions.
"use strict";

const POLL_MS = 15000;
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tClass = (st) => ({ Open: "p-open", "In Progress": "p-prog", "Escalated to Partner": "p-esc" }[st] || "p-done");
const MARK = { done: "✓", fail: "!", wait: "◷", skip: "–", lock: "⏸", info: "i" };

function fmtH(h) {
  const m = Math.round(Math.max(0, h) * 60);
  if (m < 60) return m + "m";
  if (m >= 2880) return Math.round(m / 1440) + "d";
  return Math.floor(m / 60) + "h" + (m % 60 ? " " + (m % 60) + "m" : "");
}
const hhmm = (iso) => new Date(iso).toISOString().slice(11, 16) + " UTC";

let list = [], detail = null, filter = "all", query = "", current = null, draftMode = null;

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body?.detail || res.statusText);
  return body;
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 3200);
}

// ---------------------------------------------------------------- list views
function renderSummary() {
  const c = { all: list.length, offline: 0, degraded: 0, ok: 0 };
  list.forEach((s) => c[s.status]++);
  $("#summary").innerHTML = [["all", "Stations", ""], ["offline", "Offline", "crit"], ["degraded", "Degraded", "warn"], ["ok", "Healthy", "ok"]]
    .map(([k, l, cls]) => `<button class="sum ${cls}" data-f="${k}" aria-pressed="${filter === k}"><span class="n">${c[k]}</span><span class="l">${l}</span></button>`).join("");
}
function feedAction(s) {
  const d = s.decision;
  if (d.kind === "wait") return `<span class="waitchip">Monitoring · ${d.unlock_label ? "ticket at " + esc(d.unlock_label) : "no ticket yet"}</span>`;
  if (d.kind === "none") return `<span class="waitchip">No ticket needed</span>`;
  return `<button class="linkbtn" data-new="${s.code}">Create ticket</button>`;
}
function renderFeed() {
  const rows = list.filter((s) => s.status !== "ok" && !s.active_ticket).sort((a, b) => (b.since || "").localeCompare(a.since || ""));
  $("#feed").innerHTML = rows.length ? rows.map((s) => `<div class="fi"><span class="t">${hhmm(s.since)}</span>
      <button class="c fl" data-code="${s.code}">${s.code}</button>
      <span class="m">${esc(s.condition.label)} <span class="src2">· ${fmtH(s.hours_down)} ago</span></span>${feedAction(s)}</div>`).join("")
    : `<div class="empty">Every degraded or offline station has an open ticket.</div>`;
}
function renderList() {
  const q = query.trim().toLowerCase(), rank = { offline: 0, degraded: 1, ok: 2 };
  const rows = list.filter((s) => (filter === "all" || s.status === filter) &&
      (!q || [s.code, s.router_name, ...s.ticket_keys].join(" ").toLowerCase().includes(q)))
    .sort((a, b) => rank[a.status] - rank[b.status] || a.code.localeCompare(b.code));
  $("#list").innerHTML = rows.length ? rows.map((s) => {
    const t = s.active_ticket, d = s.decision;
    const pill = t ? `<span class="pill ${tClass(t.status)}">${t.key}</span>`
      : d?.kind === "wait" ? `<span class="pill p-prog">Monitoring</span>`
      : d?.kind === "create" ? `<span class="pill p-none">No ticket</span>` : "";
    return `<button class="row" data-code="${s.code}" aria-current="${current === s.code}"><span class="stripe s-${s.status}"></span>
      <span style="min-width:0"><span class="code">${s.code}</span><div class="sub">${esc(s.condition.label)}</div></span>
      <span class="meta">${pill}${s.status !== "ok" ? `<span class="mono">${fmtH(s.hours_down)}</span>` : ""}</span></button>`;
  }).join("") : `<div class="empty">No stations match “${esc(query)}”.</div>`;
}

// ---------------------------------------------------------------- detail
function series(seed, n, base, jit, dropAt) {
  let x = seed * 9301 + 49297; const o = [];
  for (let i = 0; i < n; i++) { x = (x * 9301 + 49297) % 233280; let v = base + (x / 233280 - 0.5) * jit; if (dropAt != null && i >= dropAt) v = 0; o.push(Math.max(0, v)); }
  return o;
}
function spark(vals, max, color) {
  const w = 300, h = 56, p = 4, sx = (w - 2 * p) / (vals.length - 1), y = (v) => h - p - (v / max) * (h - 2 * p);
  const pts = vals.map((v, i) => [p + i * sx, y(v)]), last = pts.at(-1);
  const line = pts.map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="sparkline"><line x1="${p}" x2="${w - p}" y1="${y(max / 2)}" y2="${y(max / 2)}" stroke="var(--line)" stroke-dasharray="3 4"/><path d="${line} L${last[0].toFixed(1)} ${h - p} L${p} ${h - p} Z" fill="${color}" opacity=".14"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/><circle cx="${last[0]}" cy="${last[1]}" r="3" fill="${color}"/></svg>`;
}
function stepHTML(s, st) {
  const id = `cb-${s.code}-${st.id}`;
  const mark = st.manual
    ? `<input type="checkbox" class="mk-cb" id="${id}" data-step="${st.id}" ${st.state === "done" ? "checked" : ""} aria-label="Mark done: ${esc(st.title)}">`
    : `<span class="mk">${MARK[st.state]}</span>`;
  const h = st.timer && s.hold ? s.hold : null;
  const bar = h ? `<div class="hbar" role="progressbar" aria-valuemin="0" aria-valuemax="${h.hours}" aria-valuenow="${Math.min(h.hours, h.elapsed)}"><span style="width:${Math.min(100, (h.elapsed / h.hours) * 100)}%"></span></div>
      <div class="hlab mono"><span>${esc(h.since_label)} last obs</span><span>${esc(h.unlock_label)} ticket allowed</span></div>` : "";
  return `<li class="stp st-${st.state}">${mark}
    <div class="stx"><label class="stt" ${st.manual ? `for="${id}"` : ""}>${esc(st.title)}</label>
      ${st.detail ? `<div class="std">${esc(st.detail)}</div>` : ""}${st.auto ? `<div class="sta">${esc(st.auto.note)}</div>` : ""}${bar}
      ${st.code ? `<div class="cmd"><pre>${esc(st.code)}</pre><button class="cpy" data-copy="${esc(st.code)}">Copy</button></div>` : ""}
      ${st.ask && st.manual ? `<button class="btn sm" data-ask="${s.code}">Ask on Slack</button>` : ""}
      ${st.input ? `<input class="stin" id="in-${s.code}-${st.id}" data-input="${st.id}" placeholder="${esc(st.input)}" value="${esc(st.value)}">` : ""}
    </div></li>`;
}
function renderWorkflow(s) {
  const wf = s.workflow; if (!wf) return "";
  const d = s.decision, [done, total] = wf.counts;
  const net = wf.net ? `<details class="netd" ${wf.net_open ? "open" : ""}><summary>NetCloud checklist <span class="tag">${wf.net_counts[0]} of ${wf.net_counts[1]} done</span></summary>
      <ol class="steps">${wf.net.map((st) => stepHTML(s, st)).join("")}</ol></details>` : "";
  const dec = d ? `<div class="decide d-${d.kind}"><div><span class="k">Ticket rule</span><p>${esc(d.text)}</p></div>
      ${d.button ? `<button class="btn ${d.kind === "wait" ? "" : "primary"}" data-${d.kind === "comment" ? "comment" : "new"}="${s.code}">${esc(d.button)}</button>` : ""}</div>` : "";
  return `<article class="card wf"><header><h3><b>Workflow</b> · ${esc(wf.title)}</h3><span class="tag">${done} of ${total} done</span></header>
    <div class="body"><ol class="steps">${wf.steps.map((st) => stepHTML(s, st)).join("")}</ol>${net}${dec}</div></article>`;
}
function renderDetail() {
  const s = detail;
  if (!s) { $("#detail").innerHTML = `<div class="empty">Pick a station.</div>`; return; }
  const r = s.router, c = s.condition, t = s.active_ticket, o = s.owner;
  const seed = s.code.charCodeAt(0) + s.code.charCodeAt(3), allDown = s.receivers.every((x) => !x.obs);
  const cut = Math.max(0, 47 - Math.round((Math.min(s.hours_down, 6) / 6) * 47));
  const sats = series(seed, 48, s.status === "ok" ? 33 : 30, 6, allDown ? cut : null);
  const lat = series(seed + 3, 48, s.status === "ok" ? 0.9 : 1.8, 0.8, allDown ? cut : null);
  const comp = allDown ? 0 : s.status === "ok" ? 99.8 : s.receivers.every((x) => x.obs) ? 98.7 : 52.4;
  const sig = r.rsrp == null ? "" : `<dt>RSRP</dt><dd class="${r.rsrp < -105 ? "bad" : r.rsrp < -95 ? "meh" : "good"}">${r.rsrp} dBm</dd><dt>SINR</dt><dd class="${r.sinr < 3 ? "bad" : r.sinr < 10 ? "meh" : "good"}">${r.sinr} dB</dd>`;
  const ob = (x) => `<span class="ob ${x.obs ? "good" : "bad"}">Obs ${x.obs ? "✓" : "✕"}</span><span class="ob ${x.bytes ? "good" : "bad"}">Bytes ${x.bytes ? "✓" : "✕"}</span>`;
  const ask = s.inquiries[0];
  $("#detail").innerHTML = `
  <div class="dhead">
    <div class="dtitle"><h2>${s.code}</h2><span class="pill p-${s.status}">${s.status}</span>
      <span class="where">${esc(s.region_name)} · ${s.country} · ${s.receivers.length} receiver${s.receivers.length > 1 ? "s" : ""}${s.solar ? " · solar" : ""}${s.status !== "ok" ? ` · down ${fmtH(s.hours_down)}` : ""}</span></div>
    <div class="cause"><span class="k">Condition</span><span><b>${esc(c.label)}.</b> ${esc(c.hint)}</span></div>
    <div class="owner"><span class="k">Station owner</span><span><b>${esc(o.name)}</b> · ${esc(o.role)} · <span class="mono">@${esc(o.slack)}</span></span></div>
    <div class="actions">
      ${s.status !== "ok" ? `<button class="btn primary" data-ask="${s.code}">Ask ${esc(o.name.split(" ")[0])} on Slack</button>` : ""}
      ${t ? `<button class="btn" data-demo="Opens ${t.key} in Jira">Open ${t.key}</button>` : ""}
      <button class="btn" data-demo="Opens this station's Grafana dashboard">Grafana</button>
      <button class="btn" data-demo="Opens ${esc(r.name)} in NetCloud Manager">NetCloud</button>
      <button class="btn" data-demo="Opens ${esc(s.site.id)} in SiteVault">SiteVault</button>
    </div>
  </div>
  ${ask ? `<div class="ask"><div class="askh"><span class="k">Slack inquiry</span><span>Sent to <b>@${esc(ask.slack)}</b> at ${hhmm(ask.sent_at)} · awaiting reply${s.inquiries.length > 1 ? ` · ${s.inquiries.length} sent` : ""}</span></div><pre class="askm">${esc(ask.message)}</pre></div>` : ""}
  ${renderWorkflow(s)}
  <div class="cards">
    <article class="card"><header><h3><b>Router</b> · NetCloud</h3><span class="pill ${r.state === "online" ? "p-ok" : "p-offline"}">${r.state}</span></header>
      <div class="body"><dl class="kv"><dt>Device</dt><dd>${esc(r.name)}</dd><dt>Model</dt><dd>${esc(r.model)}</dd>
        <dt>Active WAN</dt><dd class="${/failover|—/.test(r.wan) ? "meh" : ""}">${esc(r.wan)}</dd>${r.carrier ? `<dt>Carrier</dt><dd>${esc(r.carrier)}</dd>` : ""}${sig}
        <dt>Last check-in</dt><dd class="${r.state === "offline" ? "bad" : ""}">${r.last_checkin ? hhmm(r.last_checkin) + " " + r.last_checkin.slice(0, 10) : "now"}</dd><dt>Firmware</dt><dd>${esc(r.firmware)}</dd>
        ${s.alerts.length ? `<dt>Log alerts</dt><dd class="bad">${esc(s.alerts.join(", "))}</dd>` : ""}</dl></div></article>
    <article class="card"><header><h3><b>Receivers</b> · Grafana</h3><span class="tag">last 6h</span></header>
      <div class="body">
        <div class="rx">${s.receivers.map((x) => `<div class="rxi"><span class="id">${esc(x.name)}</span><span class="obs">${ob(x)}</span><span class="v">${x.obs ? x.sats + " sats" : "not tracking"} · <span class="${x.temp_c >= 70 ? "bad" : ""}">${x.temp_c} °C</span></span></div>`).join("")}</div>
        <p class="obnote">Obs = real satellite data. Bytes = the receiver answers on the network. Bytes without Obs points at the antenna or receiver; both down points at the router.</p>
        <div class="spark"><div class="lab"><span>Satellites tracked</span><span class="mono">${Math.round(sats.at(-1))}</span></div>${spark(sats, 45, "var(--accent)")}</div>
        <div class="spark"><div class="lab"><span>Observation latency</span><span class="mono">${allDown ? "no data" : lat.at(-1).toFixed(2) + " s"}</span></div>${spark(lat, 4, allDown ? "var(--crit)" : "var(--warn)")}</div>
        <dl class="kv"><dt>Data completeness</dt><dd class="${comp > 99 ? "good" : comp > 90 ? "meh" : "bad"}">${comp.toFixed(1)}%</dd></dl></div></article>
    <article class="card"><header><h3><b>Site record</b> · SiteVault</h3><span class="tag">${esc(s.site.id)}</span></header>
      <div class="body"><dl class="kv"><dt>Host</dt><dd>${esc(s.site.host)}</dd><dt>Power</dt><dd>${esc(s.site.power)}</dd><dt>Site access</dt><dd>${esc(s.site.access)}</dd>
        <dt>Owner</dt><dd>${esc(o.name)}</dd><dt>Receiver LAN</dt><dd>${s.receivers.map((x) => r.lan_prefix + "." + (11 + +x.id)).join(", ")}</dd></dl></div></article>
    <article class="card"><header><h3><b>Tickets</b> · Jira</h3><span class="tag">${s.tickets.length} linked</span></header>
      <div class="body"><div class="tickets">${s.tickets.map((k) => `<button class="tk" data-demo="Opens ${k.key} in Jira"><span class="key">${k.key}</span><span><span class="pill ${tClass(k.status)}">${esc(k.status)}</span></span>
        <span class="sum2">${esc(k.summary)}</span><span class="who">${k.assignee ? esc(k.assignee) : "Unassigned"}${k.comments.length ? ` · ${k.comments.length} comment${k.comments.length > 1 ? "s" : ""}` : ""}</span></button>`).join("")}
        ${!s.tickets.length ? `<p class="noticket">No tickets on record.</p>` : ""}</div></div></article>
  </div>`;
}
async function renderActivity() {
  try {
    const ev = await api("/api/events");
    $("#activity").innerHTML = ev.length ? ev.slice(0, 8).map((e) => `<li><span class="mono">${hhmm(e.at)}</span> <button class="fl" data-code="${e.station}">${e.station}</button> ${esc(e.text)}</li>`).join("")
      : `<li class="empty">Nothing yet. The simulator breaks or recovers a station every few minutes.</li>`;
  } catch { /* non-critical */ }
}

// ---------------------------------------------------------------- data
async function refresh() {
  try {
    list = await api("/api/stations");
    if (!current && list.length) current = (list.find((s) => s.status === "offline") || list[0]).code;
    renderSummary(); renderFeed(); renderList();
    if (current) { detail = await api(`/api/stations/${current}`); renderDetail(); }
    renderActivity();
    $("#conn").textContent = "Live · updated " + new Date().toISOString().slice(11, 19) + " UTC";
  } catch (e) {
    $("#conn").textContent = "Can't reach the API: " + e.message;
  }
}
async function select(code) {
  current = code; renderList();
  detail = await api(`/api/stations/${code}`); renderDetail();
}

// ---------------------------------------------------------------- modal
async function openModal(code, mode) {
  const d = await api(`/api/stations/${code}/draft?mode=${mode}`);
  draftMode = { code, mode, ticket: d.ticket };
  $("#mh").textContent = mode === "comment" ? `Comment on ${d.ticket}` : "New station ticket";
  $("#row-sum").hidden = $("#row-meta").hidden = mode === "comment";
  $("#f-create").textContent = mode === "comment" ? "Add comment" : "Create ticket";
  $("#f-note").textContent = mode === "comment"
    ? `Ticket rule: ${d.ticket} is still open, so new findings go here instead of a new ticket.`
    : "Creates the ticket through the API (mock mode: stored in memory).";
  $("#f-sum").value = d.summary; $("#f-desc").value = d.description;
  $("#scrim").hidden = false; (mode === "comment" ? $("#f-desc") : $("#f-sum")).focus();
}
const closeModal = () => { $("#scrim").hidden = true; draftMode = null; };

async function copyText(txt, el) {
  try { await navigator.clipboard.writeText(txt); toast("Copied"); }
  catch { if (el) el.select(); toast("Select the text and press Ctrl+C"); }
}

// ---------------------------------------------------------------- events
document.addEventListener("click", async (e) => {
  const f = e.target.closest("[data-f]"); if (f) { filter = f.dataset.f; renderSummary(); renderList(); return; }
  const row = e.target.closest("[data-code]");
  if (row) { await select(row.dataset.code); if (row.classList.contains("fl") || matchMedia("(max-width:900px)").matches) $("#detail").scrollIntoView({ behavior: "smooth" }); return; }
  const n = e.target.closest("[data-new]"); if (n) return openModal(n.dataset.new, "new");
  const cm = e.target.closest("[data-comment]"); if (cm) return openModal(cm.dataset.comment, "comment");
  const ak = e.target.closest("[data-ask]");
  if (ak) {
    try { const r = await api(`/api/stations/${ak.dataset.ask}/inquiries`, { method: "POST" }); toast(`Inquiry sent to @${r.slack} (mock Slack)`); await refresh(); }
    catch (err) { toast(err.message); }
    return;
  }
  const cp = e.target.closest("[data-copy]"); if (cp) return copyText(cp.dataset.copy);
  const d = e.target.closest("[data-demo]"); if (d) return toast(d.dataset.demo + " when connected to the real tools");
  if (e.target.id === "tick") { const r = await api("/api/simulate/tick", { method: "POST" }); toast(r.station ? `${r.station}: ${r.text}` : "Nothing changed"); return refresh(); }
  if (e.target === $("#scrim")) closeModal();
});
document.addEventListener("change", async (e) => {
  const cb = e.target.closest("[data-step]"); if (!cb || !current) return;
  await api(`/api/stations/${current}/progress`, { method: "POST", body: JSON.stringify({ step: cb.dataset.step, done: cb.checked }) });
  detail = await api(`/api/stations/${current}`); renderDetail();
  document.getElementById(cb.id)?.focus();
});
document.addEventListener("focusout", (e) => {
  const i = e.target.closest?.("[data-input]"); if (!i || !current) return;
  api(`/api/stations/${current}/progress`, { method: "POST", body: JSON.stringify({ step: i.dataset.input, value: i.value }) });
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#scrim").hidden) closeModal(); });
$("#f-cancel").onclick = closeModal;
$("#f-copy").onclick = () => copyText(($("#row-sum").hidden ? "" : $("#f-sum").value + "\n\n") + $("#f-desc").value, $("#f-desc"));
$("#q").addEventListener("input", (e) => { query = e.target.value; renderList(); });
$("#tform").addEventListener("submit", async (e) => {
  e.preventDefault(); if (!draftMode) return;
  const { code, mode, ticket } = draftMode;
  try {
    if (mode === "comment") {
      await api(`/api/stations/${code}/comments`, { method: "POST", body: JSON.stringify({ body: $("#f-desc").value }) });
      toast(`Comment added to ${ticket}`);
    } else {
      const wait = list.find((s) => s.code === code)?.decision?.kind === "wait";
      const r = await api(`/api/stations/${code}/tickets`, { method: "POST", body: JSON.stringify({
        summary: $("#f-sum").value, description: $("#f-desc").value,
        source: $("#f-src").value.startsWith("Jira") ? "jira" : "sitevault", force: wait }) });
      toast(`${r.key} created`);
    }
    closeModal(); current = code; await refresh();
  } catch (err) { toast(err.message); }
});

refresh();
setInterval(refresh, POLL_MS);
