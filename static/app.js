"use strict";

const SOURCE_NAMES = {
  reference: "Reference tracks", bandcamp_follow: "Bandcamp follows", tag_feed: "Tag feeds",
  recommendation: "Recommended", boomkat: "Boomkat new",
  label_page: "Watched labels",
};
// Two inboxes, each fed by its own reference tracks; their names come from Settings.
const INBOXES = ["inbox", "inbox2"];
const isInboxView = (view = state.view) => INBOXES.includes(view);
const inboxName = (view) => state.inboxNames[INBOXES.indexOf(view)];
const views = () => [
  ["inbox", inboxName("inbox")], ["inbox2", inboxName("inbox2")],
  ["favourites", "Favourites"], ["removed", "Removed"],
];
const HEADINGS = {
  favourites: ["Favourites", "Your keeps — they stay here even after you remove them from the inbox"],
  removed: ["Removed", "Removed in the last 24 h; restore anything removed by mistake. Older ones clear out and stay blocked."],
};
const EMPTY = {
  inbox: "Inbox zero. New releases arrive with the next collector run.",
  inbox2: "Nothing here yet. Add reference tracks for this inbox in Settings; picks arrive with the next collector run.",
  favourites: "No favourites yet. Star a release to keep it here.",
  removed: "Nothing removed in the last 24 hours.",
};
const COLOURS = ["#c8553d", "#2d6a8a", "#3f7d4e", "#9a7224", "#7a4b8c", "#4a5a6a", "#8a3b3b"];
const STAR = '<path d="M10 2.5l2.3 4.8 5.2.7-3.8 3.6.9 5.2L10 14.3l-4.6 2.5.9-5.2-3.8-3.6 5.2-.7z"/>';
const PLAY_ICON = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4 2.5v11l9-5.5z"/></svg>';
const PAUSE_ICON = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><rect x="3.5" y="2.5" width="3" height="11" rx="1"/><rect x="9.5" y="2.5" width="3" height="11" rx="1"/></svg>';

const state = {
  view: "inbox",
  inboxNames: ["Inbox", "Inbox 2"],
  sort: "score",
  releases: [],
  counts: {},
  selId: null,
  detail: null,
  hiddenSources: new Set(),
  startAt: 0.5,
  waveform: true,
  lastRun: null,
  runs: [],
  // what the <audio> element holds
  player: { trackId: null, releaseId: null, title: "", artist: "", status: "idle", error: "" },
};

const $ = (sel) => document.querySelector(sel);
const audio = $("#audio");
let pendingSeek = false;
let reloadPending = false; // a collector run finished while the inbox couldn't be reloaded
let noteTimer = null;

// --- helpers ------------------------------------------------------------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) return "–:––";
  const s = Math.floor(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function colourFor(text) {
  let h = 0;
  for (const c of text || "") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return COLOURS[h % COLOURS.length];
}
function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}
function thumbUrl(url) { return url ? url.replace(/_10\.jpg$/, "_2.jpg") : null; }
function coverHtml(r, cls, url) {
  if (url) return `<img class="${cls}" src="${esc(url)}" alt=""${cls === "thumb" ? ' loading="lazy"' : ""}>`;
  const label = cls === "cover" ? "[no cover art]" : esc(initials(r.artist));
  return `<div class="${cls}" style="background:${colourFor(r.artist)}">${label}</div>`;
}
function relTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}
function belongsInView(r, view) {
  return { inbox: !r.is_removed && r.inbox === 1, inbox2: !r.is_removed && r.inbox === 2,
           favourites: r.is_favourite, removed: r.is_removed }[view];
}

// --- data ---------------------------------------------------------------

function visible() {
  let rows = state.releases.filter((r) => !state.hiddenSources.has(r.source));
  if (state.sort === "newest") {
    rows = [...rows].sort((a, b) =>
      (b.release_date || "").localeCompare(a.release_date || "") || b.found_at.localeCompare(a.found_at));
  } else {
    rows = [...rows].sort((a, b) => b.score - a.score || b.found_at.localeCompare(a.found_at));
  }
  return rows;
}

async function loadView() {
  const { data } = await api(`/api/releases?view=${state.view}`);
  state.releases = data.releases || [];
  state.counts = data.counts || {};
  const rows = visible();
  if (!rows.some((r) => r.id === state.selId)) state.selId = rows[0]?.id ?? null;
  render();
  if (state.selId) select(state.selId, { scroll: true });
  else { state.detail = null; renderDetail(); }
}

async function loadMeta() {
  const [{ data }, runs] = await Promise.all([api("/api/meta"), api("/api/runs")]);
  state.runs = runs.data.runs || [];
  state.lastRun = latestSession(state.runs);
  state.nextRun = data.next_run;
  state.inboxNames = data.inbox_names || state.inboxNames;
  state.collecting = data.collecting;
  renderRunStatus();
  state.counts = data.counts || state.counts;
  state.startAt = parseFloat(data.settings?.start_at ?? "0.5");
  state.waveform = (data.settings?.waveform ?? "1") === "1";
  $("#waveformToggle").checked = state.waveform;
  state.autoNext = (data.settings?.autonext ?? "1") === "1";
  $("#autoNextToggle").checked = state.autoNext;
  $("#startAt").value = Math.round(state.startAt * 100);
  $("#startAtValue").textContent = `${Math.round(state.startAt * 100)}%`;
}

async function select(id, { scroll = false } = {}) {
  state.selId = id;
  renderRows();
  if (scroll) document.querySelector(`.row[data-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
  const r = state.releases.find((x) => x.id === id);
  if (r && r.is_new) {
    r.is_new = false; // selecting marks it seen
    renderRows();
    api(`/api/releases/${id}`, { method: "PATCH", body: { is_new: false } });
  }
  const { ok, data } = await api(`/api/releases/${id}`);
  if (ok && state.selId === id) {
    state.detail = data;
    renderDetail();
  }
}

async function patch(id, changes) {
  const rowsBefore = visible();
  const idx = rowsBefore.findIndex((r) => r.id === id);
  const { ok, data } = await api(`/api/releases/${id}`, { method: "PATCH", body: changes });
  if (!ok) return;
  state.counts = data.counts;
  const updated = data.release;
  const tracks = updated.tracks;
  delete updated.tracks;
  const i = state.releases.findIndex((r) => r.id === id);
  if (belongsInView(updated, state.view)) {
    if (i >= 0) state.releases[i] = updated;
  } else if (i >= 0) {
    state.releases.splice(i, 1);
    if (state.selId === id) {
      const rows = visible();
      const next = rows[Math.min(idx, rows.length - 1)];
      state.selId = next?.id ?? null;
      render();
      if (next) select(next.id, { scroll: true });
      else { state.detail = null; renderDetail(); }
      return;
    }
  }
  if (state.detail?.id === id) state.detail = { ...updated, tracks, note: state.detail.note };
  render();
  renderDetail();
}

// --- rendering ----------------------------------------------------------

function render() {
  renderNav();
  renderFilters();
  renderHeading();
  renderRows();
}

function renderNav() {
  $("#views").innerHTML = views().map(([id, label]) =>
    `<button data-view="${id}" class="${state.view === id ? "on" : ""}"><span>${esc(label)}</span><span class="count">${state.counts[id] ?? 0}</span></button>`).join("");
}

function renderFilters() {
  const bySource = {};
  for (const r of state.releases) bySource[r.source] = (bySource[r.source] || 0) + 1;
  const sources = Object.keys(bySource);
  $("#sources").innerHTML = sources.length
    ? sources.map((s) => `<label><input type="checkbox" data-source="${esc(s)}" ${state.hiddenSources.has(s) ? "" : "checked"}><span>${esc(SOURCE_NAMES[s] || s)}</span><span class="n">${bySource[s]}</span></label>`).join("")
    : '<div class="muted-note">No releases in this view.</div>';

}

function renderHeading() {
  const h = isInboxView() ? inboxName(state.view) : HEADINGS[state.view];
  $("#heading").textContent = Array.isArray(h) ? h[0] : h;
  const sub = $("#subheading");
  if (!isInboxView()) { sub.textContent = h[1]; return; }
  const run = state.lastRun;
  if (!run) { sub.textContent = "No collector run yet"; return; }
  const when = run.finished_at ? `Last run ${relTime(run.finished_at)}` : `Collector running since ${relTime(run.started_at)}`;
  let html = `${esc(when)} · ${run.items_found} new`;
  if (run.errors.length) {
    html += ` · <span class="err" title="${esc(run.errors.join("\n"))}">${run.errors.length} error${run.errors.length > 1 ? "s" : ""}</span>`;
  }
  sub.innerHTML = html;
}

const STALE_HOURS = 26; // the collector runs daily at 06:00

function latestSession(runs) {
  // One `dig.collect` call runs its sources back to back; group runs that follow each other.
  if (!runs.length) return null;
  const session = [runs[0]];
  for (const r of runs.slice(1)) {
    const next = session[session.length - 1];
    if (!r.finished_at || new Date(next.started_at) - new Date(r.finished_at) > 2 * 60000) break;
    session.push(r);
  }
  return {
    started_at: session[session.length - 1].started_at,
    finished_at: session.every((r) => r.finished_at) ? session[0].finished_at : null,
    items_found: session.reduce((n, r) => n + r.items_found, 0),
    errors: session.flatMap((r) => r.errors),
  };
}

function renderNextRun() {
  const el = $("#nextRun");
  if (state.collecting) { el.textContent = "Collecting new picks right now…"; return; }
  if (!state.nextRun) { el.textContent = ""; return; }
  const at = new Date(state.nextRun);
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const time = at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const day = at.toDateString() === today.toDateString() ? "today"
    : at.toDateString() === tomorrow.toDateString() ? "tomorrow"
    : at.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const mins = Math.max(0, Math.round((at - Date.now()) / 60000));
  const until = mins < 60 ? `in ${mins} min` : `in ${Math.round(mins / 60)} h`;
  el.textContent = `Next run: ${day} ${time} · ${until}`;
  el.title = "New picks arrive with this run (while DC Digs is running). If the computer is off then, it catches up at the next start.";
}

function renderRunStatus() {
  const runs = state.runs || [];
  const last = latestSession(runs);
  let cls = "", text = "Never run — see README";
  if (last && !last.finished_at) {
    text = `Running since ${relTime(last.started_at)}`;
  } else if (last) {
    const hours = (Date.now() - new Date(last.finished_at)) / 3.6e6;
    const n = last.errors.length;
    text = `${relTime(last.finished_at)} · ${last.items_found} new`;
    if (n) text += ` · ${n} error${n > 1 ? "s" : ""}`;
    if (n && !last.items_found) cls = "bad";
    else if (n || hours > STALE_HOURS) cls = "warn";
    else cls = "ok";
    if (hours > STALE_HOURS) text = `Stale: last run ${relTime(last.finished_at)}`;
  }
  $("#runDot").className = `status-dot ${cls}`;
  $("#runText").textContent = text;
  renderNextRun();
  $("#runList").innerHTML = runs.map((r) => `<li>
      <div><span class="when">${esc(new Date(r.started_at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</span>
        · ${esc(SOURCE_NAMES[r.source] || r.source)} · ${r.finished_at ? `${r.items_found} new` : "running"}</div>
      ${r.errors.length ? `<ul class="errs">${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
    </li>`).join("") || "<li>No runs yet.</li>";
}

function renderRows() {
  const rows = visible();
  if (!rows.length) {
    const filtered = state.releases.length > 0;
    $("#rows").innerHTML = `<div class="empty">${filtered ? "Nothing matches the current source filter." : EMPTY[state.view]}${
      !filtered && state.view === "inbox" ? '<br>Run <code>.venv/bin/python -m dig.collect</code> to fetch now.' : ""}</div>`;
    return;
  }
  const inRemoved = state.view === "removed";
  $("#rows").innerHTML = rows.map((r) => {
    const genre = r.genres.find((g) => g !== "electronic") || r.genres[0] || "";
    const meta = [r.label || "", genre, SOURCE_NAMES[r.source] || r.source].filter(Boolean).join(" · ");
    const xLabel = state.view === "favourites" ? "Remove from favourites" : "Remove from inbox";
    return `<div class="row ${r.id === state.selId ? "sel" : ""}" data-id="${r.id}">
      <button class="main" data-action="select">
        <span class="dot ${r.is_new ? "new" : ""}" aria-label="${r.is_new ? "unseen" : ""}"></span>
        ${coverHtml(r, "thumb", thumbUrl(r.art_url))}
        <span class="text"><span class="t1">${esc(r.artist)} — ${esc(r.title)}</span><span class="t2">${esc(meta)}</span></span>
      </button>
      <span class="score" title="${esc(r.score_reasons.join("\n") || "no score reasons")}">${Math.round(r.score)}</span>
      <button class="icon-btn ${r.is_favourite ? "fav" : ""}" data-action="fav" aria-label="${r.is_favourite ? "Remove from favourites" : "Add to favourites"}">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="${r.is_favourite ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round">${STAR}</svg></button>
      ${inRemoved
        ? '<button class="restore" data-action="restore">Restore</button>'
        : `<button class="icon-btn" data-action="x" aria-label="${xLabel}"><svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 4l10 10M14 4L4 14"/></svg></button>`}
    </div>`;
  }).join("");
}

function renderDetail() {
  const d = state.detail;
  const el = $("#detail");
  if (!d) { el.innerHTML = ""; return; }
  const streamable = d.tracks.filter((t) => t.streamable);
  const facts = [d.label || "", d.format, d.release_date].filter(Boolean).join(" · ");
  const mainText = d.is_removed ? "Restore to inbox" : "Remove from inbox";
  el.innerHTML = `
    <div class="hero">
      ${coverHtml(d, "cover", d.art_url)}
      <div class="meta">
        <div class="eyebrow">${esc(SOURCE_NAMES[d.source] || d.source)}</div>
        <h2>${esc(d.title)}</h2>
        <div class="artist">${esc(d.artist)}</div>
        <div class="facts">${esc(facts)}</div>
        <div class="tags">${d.genres.slice(0, 8).map((g) => `<span>${esc(g)}</span>`).join("")}</div>
      </div>
    </div>

    <div class="player">
      <div class="transport">
        <button class="play" id="playBtn" aria-label="Play preview" ${streamable.length ? "" : "disabled"}>${PLAY_ICON}</button>
        <div class="bar-wrap">
          <div class="now" id="nowLine"></div>
          <div class="bar" id="seekBar" role="slider" aria-label="Seek" aria-valuemin="0" aria-valuemax="100">
            <div class="track-line"></div><div class="fill" id="fill"></div>
            <div class="wave" id="wave"></div><div class="wave played" id="wavePlayed"></div>
            <div class="start-mark" style="left:${state.startAt * 100}%" title="preview start"></div>
          </div>
        </div>
        <div class="times" id="times">starts at ${Math.round(state.startAt * 100)}%</div>
      </div>
      <div class="next-row"><button class="next-btn" id="nextBtn" type="button" aria-label="Next track"></button><span class="auto-hint" id="autoHint"></span></div>
      <ol class="tracklist">
        ${d.tracks.map((t) => `<li><button class="track-btn" data-track="${t.id}" ${t.streamable ? "" : "disabled title=\"not streamable\""}>
            <span class="pos">${t.position}</span><span class="ttl">${esc(t.title)}</span>
            <span class="dur">${t.streamable ? fmtTime(t.duration_seconds) : "not streamable"}</span></button>
            <button class="copy-btn" data-copy="${t.id}" title="Copy “${esc(copyText(t, d))}”" aria-label="Copy artist and track name">copy</button></li>`).join("")}
      </ol>
      ${streamable.length ? "" : `<div class="unstreamable">Not streamable here. <a href="${esc(d.bandcamp_url)}" target="_blank" rel="noopener">Open on Bandcamp</a></div>`}
    </div>

    <div class="actions">
      <button class="big ${d.is_favourite ? "fav-on" : ""}" data-detail="fav">
        <svg width="18" height="18" viewBox="0 0 20 20" fill="${d.is_favourite ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round">${STAR}</svg>${d.is_favourite ? "Favourited" : "Favourite"}</button>
      <button class="big" data-detail="main">${mainText}</button>
      <a href="${esc(d.bandcamp_url)}" target="_blank" rel="noopener">Open on Bandcamp</a>
    </div>

    <div class="reasons">Score ${Math.round(d.score)} — ${esc(d.score_reasons.join(" · ") || "no reasons")}</div>

    <div class="note">
      <div class="head"><label for="note" class="eyebrow">Note</label><span class="saved" id="noteSaved"></span></div>
      <textarea id="note" placeholder="e.g. opener, blend out of track 3">${esc(d.note)}</textarea>
    </div>`;
  updatePlayerUI();
}

function updatePlayerUI() {
  const p = state.player;
  const d = state.detail;
  const mine = d && p.releaseId === d.id;
  const hint = $("#autoHint");
  if (hint) {
    const left = Math.ceil(AUTO_NEXT_SECONDS - autoNext.listened);
    hint.textContent = state.autoNext && mine && !audio.paused && p.status === "playing" ? `auto-next in ${left} s` : "";
  }
  const nextBtn = $("#nextBtn");
  if (nextBtn) {
    const next = upcomingTrack(d);
    nextBtn.disabled = !next;
    const starts = next && state.player.releaseId !== d.id; // nothing from this release playing yet
    nextBtn.textContent = !next ? "⏭  Next track" : starts ? `▶  Play: ${next.title}` : `⏭  Next: ${next.title}`;
    nextBtn.title = next ? `Play “${next.title}” (N)` : "No other playable track on this release";
  }
  const btn = $("#playBtn");
  if (btn) {
    const playing = mine && !audio.paused;
    btn.innerHTML = playing ? PAUSE_ICON : PLAY_ICON;
    btn.setAttribute("aria-label", playing ? "Pause" : "Play preview");
  }
  const now = $("#nowLine");
  if (now) {
    if (mine) {
      const stateText = { loading: " · loading…", error: ` · ${p.error}`, unstreamable: " · not streamable" }[p.status] || "";
      now.innerHTML = `${esc(p.title)}<span class="state">${esc(stateText)}</span>`;
    } else if (p.trackId && !audio.paused) {
      now.innerHTML = `<span class="state">Playing: ${esc(p.artist)} — ${esc(p.title)}</span>`;
    } else {
      now.innerHTML = `<span class="state">${d && d.tracks.some((t) => t.streamable) ? "Press play or Space" : ""}</span>`;
    }
  }
  const dur = audio.duration;
  const fill = $("#fill");
  const times = $("#times");
  const progress = mine && isFinite(dur) ? (audio.currentTime / dur) * 100 : 0;
  if (fill) fill.style.width = `${progress}%`;
  const played = $("#wavePlayed");
  if (played) played.style.clipPath = `inset(0 ${100 - progress}% 0 0)`;
  renderWave();
  if (times) {
    times.textContent = mine && isFinite(dur)
      ? `${fmtTime(audio.currentTime)} / ${fmtTime(dur)}`
      : `starts at ${Math.round(state.startAt * 100)}%`;
  }
  document.querySelectorAll(".tracklist button[data-track]").forEach((b) =>
    b.classList.toggle("playing", mine && Number(b.dataset.track) === p.trackId));
}

// --- copy "Artist Track" ---------------------------------------------------

const SEPARATOR = /\s+[-–—]\s+/;

/** "Artist Track" for the clipboard, with no dash in between. On compilations and
 * label pages the track titles usually carry the artist ("Artist - Track"); then that
 * artist is used instead of the release artist. */
function copyText(track, release) {
  const title = track.title.trim();
  const artist = release.artist.trim();
  const withArtist = release.tracks.filter((t) => SEPARATOR.test(t.title)).length;
  const titlesCarryArtist = withArtist > 0 && withArtist >= release.tracks.length / 2;
  const parts = title.split(SEPARATOR);
  const text = parts.length > 1 && (titlesCarryArtist || parts[0].toLowerCase() === artist.toLowerCase())
    ? `${parts[0]} ${parts.slice(1).join(" ")}`
    : `${artist} ${title}`;
  // No ( ) either: "Feel U (Inside Me)" -> "Feel U Inside Me". The words inside stay.
  return text.replace(/[()]/g, "").replace(/\s+/g, " ").trim();
}

async function copyTrack(btn) {
  const d = state.detail;
  const t = d?.tracks.find((x) => x.id === Number(btn.dataset.copy));
  if (!t) return;
  try {
    await navigator.clipboard.writeText(copyText(t, d));
    btn.textContent = "copied";
    btn.classList.add("done");
  } catch {
    btn.textContent = "failed";
  }
  setTimeout(() => { btn.textContent = "copy"; btn.classList.remove("done"); }, 1200);
}

// --- waveform -----------------------------------------------------------
// The browser decodes the track once (through /api/tracks/<id>/audio, which only relays
// bytes) and keeps 120 loudness values; only those numbers are saved, never audio.

const WAVE_BARS = 120;
const peaksLoading = new Set();

function waveTrack() {
  // The waveform shows the track that's playing on this release, else its first playable one.
  const d = state.detail;
  if (!d) return null;
  if (state.player.releaseId === d.id) return d.tracks.find((t) => t.id === state.player.trackId) || null;
  return d.tracks.find((t) => t.streamable) || null;
}

let drawnWaveFor = null;
function renderWave() {
  const bar = $("#seekBar");
  if (!bar) return;
  const t = state.waveform ? waveTrack() : null;
  const peaks = t?.peaks;
  bar.classList.toggle("has-wave", !!peaks);
  bar.classList.toggle("wave-loading", !!t && !peaks && peaksLoading.has(t.id));
  const key = peaks ? `${t.id}` : null;
  if (key === drawnWaveFor && $("#wave").childElementCount) return;
  drawnWaveFor = key;
  const html = peaks ? peaks.map((p) => `<i style="height:${Math.max(6, Math.round(p * 100))}%"></i>`).join("") : "";
  $("#wave").innerHTML = html;
  $("#wavePlayed").innerHTML = html;
}

async function ensurePeaks(track) {
  if (!state.waveform || track.peaks || peaksLoading.has(track.id)) return;
  peaksLoading.add(track.id);
  renderWave();
  try {
    const res = await fetch(`/api/tracks/${track.id}/audio`);
    if (!res.ok) throw new Error(`audio ${res.status}`);
    const buf = await res.arrayBuffer();
    const rate = 8000; // plenty for a loudness outline, and light on memory
    const seconds = track.duration_seconds || 600;
    const ctx = new OfflineAudioContext(1, Math.ceil(seconds * rate) + rate, rate);
    const decoded = await ctx.decodeAudioData(buf);
    const data = decoded.getChannelData(0);
    const size = Math.floor(data.length / WAVE_BARS) || 1;
    const raw = [];
    for (let i = 0; i < WAVE_BARS; i++) {
      let sum = 0;
      for (let j = i * size; j < (i + 1) * size && j < data.length; j++) sum += data[j] * data[j];
      raw.push(Math.sqrt(sum / size)); // RMS: closer to perceived loudness than the peak
    }
    const max = Math.max(...raw) || 1;
    const peaks = raw.map((v) => Math.round((v / max) * 1000) / 1000);
    track.peaks = peaks;
    api(`/api/tracks/${track.id}/peaks`, { method: "PUT", body: { peaks } });
  } catch (e) {
    console.warn("waveform failed for track", track.id, e);
  } finally {
    peaksLoading.delete(track.id);
    renderWave();
  }
}

// --- player -------------------------------------------------------------

async function playTrack(track, release) {
  resetAutoNext();
  audio.pause();
  Object.assign(state.player, {
    trackId: track.id, releaseId: release.id, title: track.title, artist: release.artist, status: "loading", error: "",
  });
  updatePlayerUI();
  const { ok, data } = await api(`/api/tracks/${track.id}/stream`);
  if (state.player.trackId !== track.id) return; // user moved on meanwhile
  if (!ok) {
    state.player.status = data.streamable === false && !data.error ? "unstreamable" : "error";
    state.player.error = data.error || "couldn't get a stream URL";
    updatePlayerUI();
    return;
  }
  pendingSeek = true;
  audio.src = data.url;
  audio.load();
  ensurePeaks(track);
}

audio.addEventListener("loadedmetadata", () => {
  if (!pendingSeek) return;
  pendingSeek = false;
  if (isFinite(audio.duration)) audio.currentTime = audio.duration * state.startAt;
  audio.play().then(() => { state.player.status = "playing"; updatePlayerUI(); })
    .catch((e) => { state.player.status = "error"; state.player.error = "press play (" + e.name + ")"; updatePlayerUI(); });
});
audio.addEventListener("error", () => {
  if (!state.player.trackId) return;
  state.player.status = "error";
  state.player.error = "stream failed — try again";
  updatePlayerUI();
});
for (const ev of ["timeupdate", "play", "pause", "ended"]) audio.addEventListener(ev, updatePlayerUI);

function firstStreamable(d) { return d?.tracks.find((t) => t.streamable); }

function togglePlay() {
  const d = state.detail;
  if (!d) return;
  resetAutoNext();
  if (state.player.releaseId === d.id && audio.src && state.player.status !== "error") {
    if (audio.paused) audio.play(); else audio.pause();
    return;
  }
  const t = firstStreamable(d);
  if (t) playTrack(t, d);
}

/** Clicking a row in the inbox: play its first track straight away, and clear the row
 * above it (working down the list, that's the one you just listened to). */
async function openAndPlay(r, clicks = 1) {
  // A double click arrives as a first click, then a second one: the first already
  // plays, the second only cancels the removal it scheduled.
  if (clicks >= 2) {
    if (pendingRemoval?.rowId === r.id) cancelPendingRemoval();
    return;
  }
  if (isInboxView()) {
    const rows = visible();
    const above = rows[rows.findIndex((x) => x.id === r.id) - 1];
    if (above) scheduleRemoval(r.id, above.id);
  }
  await select(r.id);
  if (state.selId === r.id && state.player.releaseId !== r.id) togglePlay();
}

// Single click removes the row above, double click doesn't. The removal waits a moment
// so a second click can still call it off.
const DOUBLE_CLICK_MS = 350;
let pendingRemoval = null; // { rowId, aboveId, timer }

function scheduleRemoval(rowId, aboveId) {
  flushPendingRemoval(); // a click on another row: the earlier one still counts
  pendingRemoval = { rowId, aboveId, timer: setTimeout(flushPendingRemoval, DOUBLE_CLICK_MS) };
}
function flushPendingRemoval() {
  if (!pendingRemoval) return;
  const { aboveId, timer } = pendingRemoval;
  clearTimeout(timer);
  pendingRemoval = null;
  patch(aboveId, { is_removed: true, is_new: false });
}
function cancelPendingRemoval() {
  clearTimeout(pendingRemoval.timer);
  pendingRemoval = null;
}

/** The track "next" would play: the following playable one on this release (after the
 * last it goes back to the first), or its first track if another release is playing. */
function upcomingTrack(d) {
  const list = (d?.tracks || []).filter((t) => t.streamable);
  if (!list.length) return null;
  if (state.player.releaseId !== d.id) return list[0];
  const i = list.findIndex((t) => t.id === state.player.trackId);
  const next = list[(i + 1) % list.length];
  return next.id === state.player.trackId ? null : next;
}

function nextTrack() {
  const d = state.detail;
  const next = upcomingTrack(d);
  if (next) playTrack(next, d);
}

function seekBy(sec) {
  if (!state.player.trackId || !isFinite(audio.duration)) return;
  resetAutoNext();
  audio.currentTime = Math.max(0, Math.min(audio.duration - 0.5, audio.currentTime + sec));
}
function seekToFraction(f) {
  if (!state.detail || state.player.releaseId !== state.detail.id || !isFinite(audio.duration)) return;
  resetAutoNext();
  audio.currentTime = audio.duration * f;
  if (audio.paused) audio.play();
}

// --- auto-next ------------------------------------------------------------
// After AUTO_NEXT_SECONDS of playback you didn't touch (no pause, seek, next or other
// track), move on: the next playable track on the same release, or after its last one,
// the next release in the list. Unlike clicking a row, this never removes anything.

const AUTO_NEXT_SECONDS = 60;
const autoNext = { listened: 0, lastTime: null, busy: false };

function resetAutoNext() {
  autoNext.listened = 0;
  autoNext.lastTime = null;
}

audio.addEventListener("timeupdate", () => {
  if (!state.autoNext || audio.paused || state.player.status !== "playing") return;
  const t = audio.currentTime;
  const delta = autoNext.lastTime === null ? 0 : t - autoNext.lastTime;
  autoNext.lastTime = t;
  if (delta > 0 && delta < 2) autoNext.listened += delta;   // ignores jumps (seeks)
  if (autoNext.listened >= AUTO_NEXT_SECONDS) advance();
});
audio.addEventListener("ended", () => { if (state.autoNext) advance(); });

async function releaseWithTracks(id) {
  if (state.detail?.id === id) return state.detail;
  const { ok, data } = await api(`/api/releases/${id}`);
  return ok ? data : null;
}

async function advance() {
  if (autoNext.busy) return;
  autoNext.busy = true;
  resetAutoNext();
  try {
    const playingId = state.player.releaseId;
    const current = await releaseWithTracks(playingId);
    const list = (current?.tracks || []).filter((t) => t.streamable);
    const i = list.findIndex((t) => t.id === state.player.trackId);
    if (i >= 0 && i < list.length - 1) return playTrack(list[i + 1], current);

    // Last track: the next release in the list that has something to play.
    const rows = visible();
    const from = rows.findIndex((r) => r.id === playingId);
    if (from < 0) return;   // that release isn't in this list (any more); stop here
    for (const r of rows.slice(from + 1, from + 6)) {
      await select(r.id, { scroll: true });
      const t = firstStreamable(state.detail);
      if (state.selId === r.id && t) return playTrack(t, state.detail);
    }
  } finally {
    autoNext.busy = false;
  }
}

// --- actions --------------------------------------------------------------

function xAction(r) {
  if (state.view === "favourites") return patch(r.id, { is_favourite: false });
  if (state.view === "removed") return patch(r.id, { is_removed: false });
  return patch(r.id, { is_removed: true });
}

function moveSelection(delta) {
  const rows = visible();
  if (!rows.length) return;
  const i = rows.findIndex((r) => r.id === state.selId);
  const next = rows[Math.max(0, Math.min(rows.length - 1, (i < 0 ? 0 : i + delta)))];
  if (next && next.id !== state.selId) select(next.id, { scroll: true });
}

function switchView(view) {
  if (view === state.view) return;
  state.view = view;
  state.selId = null;
  loadView();
}

// --- events ---------------------------------------------------------------

$("#views").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (b) switchView(b.dataset.view);
});

$("#sources").addEventListener("change", (e) => {
  const s = e.target.dataset.source;
  if (!s) return;
  if (e.target.checked) state.hiddenSources.delete(s); else state.hiddenSources.add(s);
  renderRows();
});

document.querySelector(".sort").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-sort]");
  if (!b) return;
  state.sort = b.dataset.sort;
  document.querySelectorAll(".sort button").forEach((x) => x.classList.toggle("on", x === b));
  renderRows();
});

$("#rows").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-action]");
  const row = e.target.closest(".row");
  if (!b || !row) return;
  const r = state.releases.find((x) => x.id === Number(row.dataset.id));
  if (!r) return;
  if (b.dataset.action === "select") openAndPlay(r, e.detail);
  else if (b.dataset.action === "fav") patch(r.id, { is_favourite: !r.is_favourite });
  else if (b.dataset.action === "x") xAction(r);
  else if (b.dataset.action === "restore") patch(r.id, { is_removed: false });
});

$("#detail").addEventListener("click", (e) => {
  const d = state.detail;
  if (!d) return;
  if (e.target.closest("#playBtn")) return togglePlay();
  if (e.target.closest("#nextBtn")) return nextTrack();
  const copyBtn = e.target.closest("button[data-copy]");
  if (copyBtn) return copyTrack(copyBtn);
  const trackBtn = e.target.closest("button[data-track]");
  if (trackBtn) {
    const t = d.tracks.find((x) => x.id === Number(trackBtn.dataset.track));
    if (t) playTrack(t, d);
    return;
  }
  const bar = e.target.closest("#seekBar");
  if (bar) {
    const rect = bar.getBoundingClientRect();
    return seekToFraction(Math.max(0, Math.min(0.99, (e.clientX - rect.left) / rect.width)));
  }
  const action = e.target.closest("button[data-detail]")?.dataset.detail;
  if (action === "fav") patch(d.id, { is_favourite: !d.is_favourite });
  else if (action === "main") patch(d.id, { is_removed: !d.is_removed });
});

$("#detail").addEventListener("input", (e) => {
  if (e.target.id !== "note" || !state.detail) return;
  const id = state.detail.id;
  const note = e.target.value;
  state.detail.note = note;
  $("#noteSaved").textContent = "…";
  clearTimeout(noteTimer);
  noteTimer = setTimeout(async () => {
    const { ok } = await api(`/api/releases/${id}`, { method: "PATCH", body: { note } });
    const r = state.releases.find((x) => x.id === id);
    if (r) r.note = note;
    const saved = $("#noteSaved");
    if (saved && state.detail?.id === id) saved.textContent = ok ? "saved" : "not saved!";
  }, 600);
});

$("#startAt").addEventListener("input", (e) => {
  state.startAt = Number(e.target.value) / 100;
  $("#startAtValue").textContent = `${e.target.value}%`;
  document.querySelector(".start-mark")?.style.setProperty("left", `${e.target.value}%`);
  updatePlayerUI();
});
$("#startAt").addEventListener("change", () => api("/api/settings", { method: "PUT", body: { start_at: state.startAt } }));
$("#autoNextToggle").addEventListener("change", (e) => {
  state.autoNext = e.target.checked;
  resetAutoNext();
  api("/api/settings", { method: "PUT", body: { autonext: state.autoNext } });
  updatePlayerUI();
});
$("#waveformToggle").addEventListener("change", (e) => {
  state.waveform = e.target.checked;
  api("/api/settings", { method: "PUT", body: { waveform: state.waveform } });
  drawnWaveFor = undefined;
  renderWave();
  const t = waveTrack();
  if (state.waveform && t && state.player.trackId === t.id) ensurePeaks(t);
});

function isTyping(e) {
  const t = e.target;
  return t.closest && (t.closest("textarea, input, select, [contenteditable]"));
}

// --- settings pop-up ---------------------------------------------------------
// The settings page in a dialog over the inbox, so playback and the list stay put.

const settingsDialog = $("#settingsDialog");
let settingsChanged = false;

$("#openSettings").addEventListener("click", (e) => {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // new tab/window: full page
  e.preventDefault();
  settingsChanged = false;
  $("#settingsFrame").src = "/settings?embed=1";
  settingsDialog.showModal();
});
function closeSettings() {
  if (settingsDialog.open) settingsDialog.close();
  afterSettingsClosed();
}

// Runs once per close, whichever way it was closed. (Not only from the dialog's own
// "close" event: browsers fire that during rendering, i.e. late or never in a hidden tab.)
async function afterSettingsClosed() {
  if (settingsDialog.open) return;                     // reopened meanwhile
  $("#settingsFrame").src = "about:blank";
  if (!settingsChanged) return;
  settingsChanged = false;                             // saved criteria re-rank the inbox
  await loadMeta();
  if (isInboxView()) await loadView();
  else { renderNav(); renderHeading(); }
}

$("#closeSettings").addEventListener("click", closeSettings);
settingsDialog.addEventListener("click", (e) => { if (e.target === settingsDialog) closeSettings(); });
settingsDialog.addEventListener("close", afterSettingsClosed);   // Esc on the inbox side
window.addEventListener("message", (e) => {
  if (e.origin !== location.origin) return;
  if (e.data === "dig:settings-saved") settingsChanged = true;
  if (e.data === "dig:close-settings") closeSettings();
});

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e) || settingsDialog.open) return;
  const r = state.releases.find((x) => x.id === state.selId);
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  let handled = true;
  if (key === " ") togglePlay();
  else if (key === "ArrowLeft") seekBy(-30);
  else if (key === "ArrowRight") seekBy(30);
  else if (/^[1-9]$/.test(key)) seekToFraction(Number(key) / 10);
  else if (key === "j" || key === "ArrowDown") moveSelection(1);
  else if (key === "k" || key === "ArrowUp") moveSelection(-1);
  else if (key === "n") nextTrack();
  else if (key === "f" && r) patch(r.id, { is_favourite: !r.is_favourite });
  else if (key === "x" && r) xAction(r);
  else handled = false;
  if (handled) e.preventDefault();
});
// Stop Space from also "clicking" whichever button has focus.
document.addEventListener("keyup", (e) => { if (e.key === " " && !isTyping(e)) e.preventDefault(); });

// --- start ----------------------------------------------------------------

(async () => {
  const { data: first } = await api("/api/meta");
  if (first.setup_needed) { location.href = "/setup"; return; }
  await loadMeta();
  await loadView();
  // Pick up the collector's new releases and run status without a reload.
  setInterval(async () => {
    const runKey = () => `${state.lastRun?.started_at}:${state.lastRun?.finished_at}`;
    const before = runKey();
    await loadMeta();
    if (runKey() !== before && state.lastRun?.finished_at) reloadPending = true;
    // Don't rebuild the inbox under someone typing a note; try again next minute.
    if (reloadPending && isInboxView() && document.activeElement?.id !== "note") {
      reloadPending = false;
      await loadView();
    }
    renderNav();
    renderHeading();
  }, 60000);
})();
