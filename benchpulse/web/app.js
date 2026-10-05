// BenchPulse dashboard: polls the hub, draws the PC cards, the week schedule and who's who.
// No framework, no build step: the hub serves this file as is.
"use strict";

const $ = (s) => document.querySelector(s);
const STATUS = { online: "Online", in_use: "In use", missing: "Idle / missing" };
const POLL_MS = 3000;
let board = { pcs: [], counts: {} }, filter = "all", weekStart = monday(new Date()), lastOk = 0, reservations = [];

// ------------------------------------------------------------------ helpers
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);   // strings become text: no HTML injection
  return el;
}
const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const stamp = (d) => `${ymd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
function monday(d) { const m = new Date(d.getFullYear(), d.getMonth(), d.getDate()); m.setDate(m.getDate() - ((m.getDay() + 6) % 7)); return m; }
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const hm = (s) => s.slice(11, 16);
const initials = (s) => s.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
function ago(sec) {
  if (sec == null) return "never seen";
  if (sec < 60) return `${sec} s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} d ago`;
}
const dur = (s) => (s == null ? "" : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`);

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json" }, body: opts.body && JSON.stringify(opts.body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2200); }

function copy(text) {
  // navigator.clipboard needs https or localhost; the hub is plain http on the LAN, so fall back.
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(() => toast(`Copied ${text}`));
  const ta = h("textarea", { style: "position:fixed;opacity:0" }, text);
  document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast(`Copied ${text}`);
}

// ------------------------------------------------------------------ cards
function usageBlock(pc) {
  const u = pc.usage || {};
  if (pc.status === "missing")
    return h("div", { class: "usage" }, h("div", { class: "avatar free" }, "–"),
      h("div", {}, h("div", { class: "who" }, "No heartbeat"), h("div", { class: "how" }, pc.seen_ago == null ? "Has never reported: is its agent installed?" : `Off, asleep or agent not running · last seen ${ago(pc.seen_ago)}`)));
  if (u.kind === "remote") {
    const who = pc.person || u.client_name || u.user;
    const from = [u.client_name, u.client_ip].filter(Boolean).join(" · ");
    return h("div", { class: "usage" }, h("div", { class: "avatar" }, initials(who)),
      h("div", {}, h("div", { class: "who" }, who), h("div", { class: "how" }, `Remote Desktop from ${from} · signed in as ${u.user}`)));
  }
  if (u.kind === "local")
    return h("div", { class: "usage" }, h("div", { class: "avatar" }, initials(pc.person || u.user)),
      h("div", {}, h("div", { class: "who" }, pc.person || u.user), h("div", { class: "how" }, `At the PC · last input ${dur(u.idle_s) || "just now"} ago`)));
  const idle = u.kind === "idle" ? `${u.user} is signed in but idle${u.idle_s != null ? ` for ${dur(u.idle_s)}` : ""}` : "Nobody is using it";
  return h("div", { class: "usage" }, h("div", { class: "avatar free" }, "✓"),
    h("div", {}, h("div", { class: "who" }, "Free"), h("div", { class: "how" }, idle)));
}

function bookingLine(label, r) {
  if (!r) return null;
  const day = r.start.slice(0, 10) === ymd(new Date()) ? "" : new Date(r.start).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + " ";
  return h("div", { class: "book" }, h("span", { class: "lbl" }, label),
    h("span", {}, h("b", {}, r.who), ` · ${day}${hm(r.start)}–${hm(r.end)}`, r.purpose ? ` · ${r.purpose}` : ""));
}

function card(pc) {
  const u = pc.usage || {};
  const note = h("div", { class: "note", title: "Click to edit the note", onclick: () => editNote(pc) }, pc.note || "");
  return h("article", { class: `card ${pc.status}` },
    h("div", { class: "top" },
      h("span", { class: "pill" }, h("span", { class: "dot" }), STATUS[pc.status]),
      h("span", { class: "seen", title: pc.last_seen ? new Date(pc.last_seen * 1000).toLocaleString() : "" },
        pc.status === "missing" ? "" : `up ${dur(pc.uptime_s)}`),
      pc.status === "missing" ? h("button", { class: "icon-btn", title: "Remove this PC from the board", onclick: () => forget(pc) }, "✕") : null),
    h("div", { class: "namerow" },
      h("span", { class: "name" }, pc.name),
      h("button", { class: "icon-btn", title: "Copy the name for Remote Desktop", onclick: () => copy(pc.name) }, "⧉")),
    h("div", { class: "meta" }, h("span", { class: "ip" }, pc.ip || "—"), pc.os ? h("span", {}, pc.os.replace(/-/g, " ")) : null),
    note,
    usageBlock(pc),
    pc.clash ? h("div", { class: "clash" }, `⚠ Booked for ${pc.reserved_now.who} right now, but ${pc.person} is on it`) : null,
    bookingLine("Booked now", pc.reserved_now),
    bookingLine("Next", pc.reserved_next),
    h("div", { class: "actions-row" },
      h("a", { class: `btn${pc.status === "missing" ? " disabled" : ""}`, href: `/rdp/${encodeURIComponent(pc.name)}.rdp`,
        title: "Download a Remote Desktop file for this PC" }, "🖥 Connect"),
      h("button", { class: "btn", onclick: () => openBooking({ pc: pc.name }) }, "📅 Reserve")));
}

function matches(pc, q) {
  if (!q) return true;
  const u = pc.usage || {};
  return [pc.name, pc.ip, pc.note, pc.person, u.user, u.client_name, u.client_ip, pc.reserved_now?.who]
    .some((v) => v && v.toLowerCase().includes(q));
}

function renderBoard() {
  const c = board.counts || {};
  $("#counts").replaceChildren(
    ...["online", "in_use", "missing"].map((s) => h("span", { class: `count ${s}` },
      h("span", { class: "dot" }), String(c[s] ?? 0), h("small", {}, STATUS[s]))));
  const q = $("#search").value.trim().toLowerCase();
  const shown = board.pcs.filter((p) => (filter === "all" || p.status === filter) && matches(p, q));
  $("#grid").replaceChildren(...shown.map(card));
  const empty = $("#empty");
  empty.hidden = shown.length > 0;
  empty.textContent = board.pcs.length ? "No PC matches." : "No PCs yet. Start the agent on a bench PC:  python -m benchpulse agent --hub http://<this-hub>:8600";
  $("#version").textContent = board.version ? `v${board.version}` : "";
}

async function poll() {
  try {
    const next = await api("/api/board");
    lastOk = Date.now();
    // "seen x s ago" changes every poll; only redraw when something visible did
    const key = JSON.stringify(next, (k, v) => (k === "seen_ago" || k === "last_seen" ? undefined : k === "uptime_s" ? Math.round(v / 3600) : v));
    board = next;
    if (key !== poll.key) { poll.key = key; renderBoard(); renderSchedule(); }
  } catch (e) { /* shown as stale below */ }
  const age = Math.round((Date.now() - lastOk) / 1000);
  const up = $("#updated");
  up.textContent = !lastOk ? "connecting…" : age < 6 ? "live" : `hub unreachable · ${age} s`;
  up.classList.toggle("stale", !lastOk || age >= 6);
}

// ------------------------------------------------------------------ schedule
async function loadReservations() {
  reservations = await api(`/api/reservations?from=${stamp(weekStart)}&to=${stamp(addDays(weekStart, 7))}`);
  renderSchedule();
}

function renderSchedule() {
  const days = [...Array(7)].map((_, i) => addDays(weekStart, i));
  const today = ymd(new Date()), now = stamp(new Date());
  $("#weekLabel").textContent = `${days[0].toLocaleDateString(undefined, { day: "numeric", month: "short" })} – ${days[6].toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
  const names = [...new Set([...board.pcs.map((p) => p.name), ...reservations.map((r) => r.pc)])];
  const statusOf = Object.fromEntries(board.pcs.map((p) => [p.name.toLowerCase(), p.status]));
  const head = h("tr", {}, h("th", {}, "PC"), ...days.map((d) => h("th", { class: ymd(d) === today ? "today" : "" },
    d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }))));
  const rows = names.map((name) => h("tr", {},
    h("td", { class: `pc ${statusOf[name.toLowerCase()] || "missing"}` },
      h("span", { class: "dot" }), name),
    ...days.map((d) => {
      const day = ymd(d);
      const mine = reservations.filter((r) => r.pc.toLowerCase() === name.toLowerCase() && r.start.slice(0, 10) <= day && r.end.slice(0, 10) >= day && !(r.end.slice(0, 10) === day && r.end.slice(11) === "00:00"));
      return h("td", { class: `slot${day === today ? " today" : ""}`, title: `Book ${name} on ${day}`,
        onclick: (e) => { if (e.target === e.currentTarget) openBooking({ pc: name, day }); } },
        ...mine.map((r) => h("span", { class: `res${r.start <= now && now < r.end ? " now" : r.end <= now ? " past" : ""}`, onclick: () => showBooking(r) },
          h("b", {}, `${hm(r.start)}–${hm(r.end)}`), ` ${r.who}`, r.purpose ? h("span", {}, r.purpose) : null)));
    })));
  $("#schedule").replaceChildren(h("thead", {}, head), h("tbody", {}, ...rows));
}

// ------------------------------------------------------------------ bookings
function openBooking({ pc, day } = {}) {
  const f = $("#bookForm");
  const sel = f.elements.pc;
  sel.replaceChildren(...board.pcs.map((p) => h("option", { value: p.name }, `${p.name} · ${STATUS[p.status]}`)));
  if (pc) sel.value = pc;
  const now = new Date();
  const start = new Date(now); start.setMinutes(Math.ceil(now.getMinutes() / 15) * 15, 0, 0);
  f.elements.day.value = day || ymd(start);
  f.elements.from.value = day && day !== ymd(now) ? "09:00" : `${pad(start.getHours())}:${pad(start.getMinutes())}`;
  const end = new Date(start.getTime() + 2 * 3600e3);
  f.elements.to.value = day && day !== ymd(now) ? "11:00" : `${pad(end.getHours())}:${pad(end.getMinutes())}`;
  f.elements.who.value = localStorage.getItem("benchpulse.who") || "";
  f.elements.purpose.value = "";
  $("#bookError").textContent = "";
  $("#bookDialog").showModal();
  (f.elements.who.value ? f.elements.purpose : f.elements.who).focus();
}

$("#bookForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();                                    // keep the dialog open until the hub says yes
  const f = e.target.elements;
  const body = { pc: f.pc.value, who: f.who.value.trim(), purpose: f.purpose.value.trim(),
    start: `${f.day.value}T${f.from.value}`, end: `${f.day.value}T${f.to.value}` };
  if (f.to.value <= f.from.value) body.end = `${ymd(addDays(new Date(f.day.value + "T00:00"), 1))}T${f.to.value}`;   // overnight booking
  try {
    await api("/api/reservations", { method: "POST", body });
    try { localStorage.setItem("benchpulse.who", body.who); } catch { /* private window */ }
    $("#bookDialog").close();
    toast(`${body.pc} reserved for ${body.who}`);
    weekStart = monday(new Date(body.start));
    await loadReservations(); poll();
  } catch (err) { $("#bookError").textContent = err.message; }
});

function showBooking(r) {
  $("#infoTitle").textContent = `${r.pc} · ${r.who}`;
  const d = new Date(r.start);
  $("#infoBody").replaceChildren(
    h("p", {}, d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }), `, ${hm(r.start)}–${hm(r.end)}`),
    r.purpose ? h("p", {}, r.purpose) : null);
  const dlg = $("#infoDialog");
  dlg.onclose = async () => {
    if (dlg.returnValue !== "delete") return;
    if (!confirm(`Cancel ${r.who}'s booking of ${r.pc}?`)) return;
    try { await api(`/api/reservations/${r.id}`, { method: "DELETE" }); toast("Booking cancelled"); await loadReservations(); poll(); }
    catch (err) { toast(err.message); }
  };
  dlg.showModal();
}

async function editNote(pc) {
  const note = prompt(`Note for ${pc.name} (rack, hardware, who to ask…)`, pc.note || "");
  if (note === null) return;
  await api(`/api/pcs/${encodeURIComponent(pc.name)}/note`, { method: "POST", body: { note } }).catch((e) => toast(e.message));
  poll();
}

async function forget(pc) {
  if (!confirm(`Remove ${pc.name} from the board? It comes back by itself if its agent reports again.`)) return;
  await api(`/api/pcs/${encodeURIComponent(pc.name)}`, { method: "DELETE" });
  poll();
}

// ------------------------------------------------------------------ who's who
async function loadPeople() {
  const people = await api("/api/people");
  $("#people").replaceChildren(...people.map((p) => h("tr", {},
    h("td", {}, p.key), h("td", {}, p.person),
    h("td", {}, h("button", { class: "icon-btn", title: "Remove", onclick: async () => { await api(`/api/people/${encodeURIComponent(p.key)}`, { method: "DELETE" }); loadPeople(); poll(); } }, "✕")))));
}
$("#peopleForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  try { await api("/api/people", { method: "POST", body: { key: f.key.value.trim(), person: f.person.value.trim() } }); e.target.reset(); loadPeople(); poll(); }
  catch (err) { toast(err.message); }
});

// ------------------------------------------------------------------ wiring
$("#filters").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  filter = b.dataset.f;
  document.querySelectorAll("#filters button").forEach((x) => x.classList.toggle("on", x === b));
  renderBoard();
});
$("#search").addEventListener("input", renderBoard);
document.addEventListener("keydown", (e) => { if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("#search").focus(); } });
$("#prevWeek").onclick = () => { weekStart = addDays(weekStart, -7); loadReservations(); };
$("#nextWeek").onclick = () => { weekStart = addDays(weekStart, 7); loadReservations(); };
$("#thisWeek").onclick = () => { weekStart = monday(new Date()); loadReservations(); };
$("#newBooking").onclick = () => openBooking();

poll().then(loadReservations);
loadPeople();
setInterval(poll, POLL_MS);
setInterval(() => loadReservations().catch(() => {}), 30000);   // pick up bookings made by colleagues
