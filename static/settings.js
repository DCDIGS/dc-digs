"use strict";

const $ = (s) => document.querySelector(s);
let crit = null;
// Shown inside the inbox's settings pop-up: no page header, and report saves to the inbox.
const embedded = new URLSearchParams(location.search).has("embed") && window.parent !== window;
if (embedded) document.body.classList.add("embedded");
function notifyInbox(msg) { if (embedded) window.parent.postMessage(msg, location.origin); }
if (embedded) {
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") notifyInbox("dig:close-settings"); });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function getPath(obj, path) { return path.split(".").reduce((o, k) => o?.[k], obj); }
function setPath(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  keys.reduce((o, k) => (o[k] ??= {}), obj)[last] = value;
}
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
}
function status(el, text, cls = "") { el.textContent = text; el.className = `status ${cls}`; }

// --- chip lists -------------------------------------------------------------

const chipValues = {};

// "https://artist.bandcamp.com/track/some-track" -> "artist / some-track"
function shortRef(url) {
  const m = url.match(/^https?:\/\/([^./]+)[^/]*\/(?:track|album)\/([^/?#]+)/);
  return m ? `${m[1]} / ${m[2]}` : url;
}

function renderChips(box) {
  const path = box.dataset.list;
  const link = box.dataset.link;
  box.innerHTML = chipValues[path].map((v, i) => `<span class="chip">${
    link === "" ? `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(shortRef(v))}</a>`
    : link ? `<a href="${esc(link + encodeURIComponent(v))}" target="_blank" rel="noopener">${esc(v)}</a>` : esc(v)
  }<button type="button" data-remove="${i}" aria-label="Remove ${esc(v)}">×</button></span>`).join("")
    + `<input type="text" placeholder="add…" aria-label="Add to ${esc(path)}">`;
}

function addChip(box, text) {
  const path = box.dataset.list;
  for (const part of text.split(/[,\n]/)) {
    const v = part.trim();
    if (v && !chipValues[path].includes(v)) chipValues[path].push(v);
  }
  renderChips(box);
  box.querySelector("input").focus();
}

document.querySelectorAll(".chips").forEach((box) => {
  box.addEventListener("click", (e) => {
    const rm = e.target.closest("[data-remove]");
    if (rm) {
      chipValues[box.dataset.list].splice(Number(rm.dataset.remove), 1);
      renderChips(box);
    } else if (e.target === box) box.querySelector("input").focus();
  });
  box.addEventListener("keydown", (e) => {
    if (e.target.tagName !== "INPUT") return;
    if ((e.key === "Enter" || e.key === ",") && e.target.value.trim()) {
      e.preventDefault();
      addChip(box, e.target.value);
    } else if (e.key === "Backspace" && !e.target.value && chipValues[box.dataset.list].length) {
      chipValues[box.dataset.list].pop();
      renderChips(box);
      box.querySelector("input").focus();
    } else if (e.key === "Enter") e.preventDefault();
  });
  box.addEventListener("paste", (e) => {
    if (e.target.tagName !== "INPUT") return;
    e.preventDefault();
    addChip(box, e.clipboardData.getData("text"));
  });
  // Text typed but not confirmed with Enter still counts when saving.
  box.addEventListener("focusout", (e) => {
    if (e.target.tagName === "INPUT" && e.target.value.trim()) addChip(box, e.target.value);
  });
});

// --- boost / weight editors ----------------------------------------------------

function pairRow(tag = "", points = 1) {
  return `<div class="pair"><input class="tag" type="text" value="${esc(tag)}" placeholder="tag, e.g. rominimal" aria-label="Tag">
    <input class="pts" type="number" value="${points}" min="-20" max="20" aria-label="Points">
    <button type="button" class="icon-x" aria-label="Remove">×</button></div>`;
}

$("#addBoost").addEventListener("click", () => {
  $("#boostTags").insertAdjacentHTML("beforeend", pairRow("", 1));
  $("#boostTags .pair:last-child .tag").focus();
});
$("#boostTags").addEventListener("click", (e) => { if (e.target.closest(".icon-x")) e.target.closest(".pair").remove(); });

const SOURCE_NAMES = {
  reference: "Reference tracks", bandcamp_follow: "Bandcamp follows", tag_feed: "Tag feeds", recommendation: "Recommended",
  label_page: "Watched labels", boomkat: "Boomkat",
};

// --- load / collect / save ---------------------------------------------------------

function fill(c) {
  crit = c;
  document.querySelectorAll("#form [name]").forEach((el) => {
    const v = getPath(c, el.name);
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  });
  document.querySelectorAll(".chips").forEach((box) => {
    chipValues[box.dataset.list] = [...(getPath(c, box.dataset.list) || [])];
    renderChips(box);
  });
  document.querySelectorAll("#formats input").forEach((el) => { el.checked = c.filters.formats.includes(el.value); });
  $("#boostTags").innerHTML = Object.entries(c.ranking.boost_tags).map(([t, p]) => pairRow(t, p)).join("");
  $("#sourceWeights").innerHTML = Object.entries(c.ranking.source_weight).map(([s, w]) =>
    `<label class="num"><span>${esc(SOURCE_NAMES[s] || s)}</span><input type="number" data-source="${esc(s)}" value="${w}" min="-20" max="20"></label>`).join("");
}

function collectForm() {
  const c = structuredClone(crit);
  document.querySelectorAll("#form [name]").forEach((el) => {
    setPath(c, el.name, el.type === "checkbox" ? el.checked : el.value);
  });
  for (const [path, values] of Object.entries(chipValues)) setPath(c, path, values);
  c.filters.formats = [...document.querySelectorAll("#formats input:checked")].map((el) => el.value);
  c.ranking.boost_tags = {};
  document.querySelectorAll("#boostTags .pair").forEach((row) => {
    const tag = row.querySelector(".tag").value.trim();
    if (tag) c.ranking.boost_tags[tag] = row.querySelector(".pts").value;
  });
  c.ranking.source_weight = {};
  document.querySelectorAll("#sourceWeights input").forEach((el) => { c.ranking.source_weight[el.dataset.source] = el.value; });
  return c;
}

async function load() {
  const { data } = await api("/api/criteria");
  fill(data.criteria);
  $("#raw").value = data.raw;
}

async function save({ collectAfter = false } = {}) {
  const st = $("#status");
  status(st, "Saving…");
  const { ok, data } = await api("/api/criteria", { method: "PUT", body: { criteria: collectForm() } });
  if (!ok) { status(st, data.error || "Couldn't save", "err"); return false; }
  fill(data.criteria);
  $("#raw").value = data.raw;
  const warn = data.warnings?.length ? ` · ${data.warnings.join(" · ")}` : "";
  status(st, `Saved${warn}`, warn ? "err" : "ok");
  notifyInbox("dig:settings-saved");
  if (collectAfter) await startCollect();
  return true;
}

async function startCollect() {
  const st = $("#status");
  const { ok, data } = await api("/api/collect", { method: "POST" });
  if (!ok) { status(st, data.reason || "Couldn't start the collector", "err"); return; }
  $("#saveCollect").disabled = true;
  status(st, "Saved · collecting… (you can keep working; the inbox updates by itself)", "ok");
  const timer = setInterval(async () => {
    const { data: s } = await api("/api/collect");
    if (!s.running) {
      clearInterval(timer);
      $("#saveCollect").disabled = false;
      const { data: runs } = await api("/api/runs");
      const recent = (runs.runs || []).filter((r) => Date.now() - new Date(r.started_at) < 15 * 60000);
      const added = recent.reduce((n, r) => n + r.items_found, 0);
      const errors = recent.flatMap((r) => r.errors);
      status(st, `Collected: ${added} new release${added === 1 ? "" : "s"}${errors.length ? ` · ${errors.length} error(s)` : ""}`,
        errors.length ? "err" : "ok");
    }
  }, 3000);
}

$("#form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
$("#saveCollect").addEventListener("click", () => save({ collectAfter: true }));

$("#saveRaw").addEventListener("click", async () => {
  const st = $("#rawStatus");
  status(st, "Saving…");
  const { ok, data } = await api("/api/criteria/raw", { method: "PUT", body: { raw: $("#raw").value } });
  if (!ok) { status(st, data.error || "Couldn't save", "err"); return; }
  fill(data.criteria);
  const warn = data.warnings?.length ? ` · ${data.warnings.join(" · ")}` : "";
  status(st, `Saved${warn}`, warn ? "err" : "ok");
  notifyInbox("dig:settings-saved");
});

load();
