"use strict";

const $ = (s) => document.querySelector(s);
const lines = (text) => text.split(/[\n,]+/).map((l) => l.trim()).filter(Boolean);

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  return { ok: res.ok, data: await res.json().catch(() => ({})) };
}
function status(text, cls = "") { const el = $("#status"); el.textContent = text; el.className = `status ${cls}`; }

(async () => {
  const { data } = await api("/api/setup");
  $("#username").value = data.bandcamp_username || "";
  $("#name1").value = data.inbox_names?.[0] || "";
  $("#name2").value = data.inbox_names?.[1] || "";
  $("#refs1").value = (data.reference_tracks || []).join("\n");
  $("#refs2").value = (data.reference_tracks_2 || []).join("\n");
  $("#dailyAt").value = data.daily_at || "06:00";
  $("#refs1").focus();
})();

$("#setup").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#go").disabled = true;
  status("Checking…");
  const { ok, data } = await api("/api/setup", { method: "POST", body: {
    bandcamp_username: $("#username").value.trim(),
    inbox_names: [$("#name1").value.trim(), $("#name2").value.trim()],
    reference_tracks: lines($("#refs1").value),
    reference_tracks_2: lines($("#refs2").value),
    daily_at: $("#dailyAt").value,
    collect_now: true,
  } });
  if (!ok) {
    $("#go").disabled = false;
    status(data.error || "Something went wrong. Please check your entries.", "err");
    return;
  }
  status(data.collecting
    ? "Saved. The first dig has started: it takes a few minutes, and your inbox fills up as it goes."
    : "Saved.", "ok");
  setTimeout(() => { location.href = "/"; }, 2500);
});
