// API + DOM helpers
const base = window.location.pathname.replace(/\/$/, "");
const api = (p) => `${base}/api/${p}`;
const $ = (id) => document.getElementById(id);

// Mode icon mapping
const modeIcon = (mode) =>
  (mode || "").toLowerCase() === "active" ? "⚡" : "📝";

// Truncate helper
const snippet = (text, max = 120) => {
  if (!text) return "";
  const t = String(text).trim().replace(/\s+/g, " ");
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
};

// -------- Renderers --------
function renderStatus(data) {
  const count = data.event_count ?? 0;
  $("eventCount").textContent = `Events since last analysis: ${count}`;
  if (data.seconds_since_last != null) {
    const sec = Math.floor(data.seconds_since_last);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    $("sinceLast").textContent = `Time since last analysis: ${h}h ${m}m`;
  } else {
    $("sinceLast").textContent = "Time since last analysis: N/A";
  }
}

function renderGrid(rows) {
  const grid = $("analysisGrid");
  grid.innerHTML = "";

  rows.forEach((row) => {
    const r = Array.isArray(row)
      ? { id: row[0], ts: row[1], mode: row[2], focus: row[3], summary: row[4], actions: row[5] }
      : row;

    const icon = modeIcon(r.mode);
    const btn = document.createElement("button");
    btn.className =
      "w-full text-left p-4 rounded-xl border border-white/10 bg-gray-800/60 hover:bg-gray-800 transition-colors shadow group";
    btn.innerHTML = `
      <div class="flex items-start gap-3">
        <div class="text-2xl">${icon}</div>
        <div class="flex-1">
          <div class="flex items-center justify-between">
            <div class="font-semibold text-gray-100">${r.mode ?? "passive"}</div>
            <div class="text-xs text-gray-400">${r.ts ?? ""}</div>
          </div>
          <div class="mt-1 text-sm text-gray-300">${snippet(r.summary)}</div>
          ${r.focus ? `<div class="mt-2 inline-flex items-center text-[11px] px-2 py-0.5 rounded bg-indigo-600/20 text-indigo-300">🎯 ${r.focus}</div>` : ""}
        </div>
      </div>
    `;
    btn.addEventListener("click", () => openModal(r));
    grid.appendChild(btn);
  });
}

// -------- Data loaders --------
async function jsonFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${url}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

async function loadStatus() {
  const data = await jsonFetch(api("status"));
  $("toggleMode").textContent = data.mode || "passive";
  renderStatus(data);
}

async function loadHistory() {
  let rows = await jsonFetch(api("history"));
  if (!rows) rows = [];
  const dataRows = Array.isArray(rows) ? rows : Object.values(rows);
  renderGrid(dataRows);
}

// -------- Interactions --------
async function toggleMode() {
  const cur = $("toggleMode").textContent.trim().toLowerCase();
  const next = cur === "active" ? "passive" : "active";
  await jsonFetch(api(`mode?mode=${encodeURIComponent(next)}`), { method: "POST" });
  await loadStatus();
}

async function runAnalysisNow() {
  const mode = $("toggleMode").textContent.trim().toLowerCase() || "passive";
  const bar = $("progressBar");
  bar.style.width = "0%";
  requestAnimationFrame(() => (bar.style.width = "25%"));
  try {
    await jsonFetch(api("run"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    bar.style.width = "100%";
    await loadHistory();
    await loadStatus();
  } catch (e) {
    console.error("runAnalysis failed:", e);
    bar.style.width = "100%";
    await loadHistory();
    await loadStatus();
  }
}

// -------- Modal --------
function openModal(row) {
  const overlay = $("detailsOverlay");
  const title = $("modalTitle");
  const meta = $("modalMeta");
  const summary = $("modalSummary");

  title.textContent = `${modeIcon(row.mode)} ${row.mode ?? "passive"} summary`;
  meta.textContent = [row.ts, row.focus ? `Focus: ${row.focus}` : ""].filter(Boolean).join(" • ");
  summary.textContent = row.summary ?? "(No summary)";

  overlay.classList.remove("hidden");
  document.addEventListener("keydown", escClose);
  $("overlayBackdrop").addEventListener("click", closeModal, { once: true });
  $("modalClose").addEventListener("click", closeModal, { once: true });
}

function escClose(e) {
  if (e.key === "Escape") closeModal();
}
function closeModal() {
  $("detailsOverlay").classList.add("hidden");
  document.removeEventListener("keydown", escClose);
}

// -------- Init --------
function init() {
  $("toggleMode").addEventListener("click", toggleMode);
  $("runAnalysis").addEventListener("click", runAnalysisNow);
  loadStatus().catch(console.error);
  loadHistory().catch(console.error);
  setInterval(() => {
    loadStatus().catch(console.error);
    loadHistory().catch(console.error);
  }, 10000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
