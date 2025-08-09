// ---------- API + DOM helpers ----------
const base = window.location.pathname.replace(/\/$/, "");
const api  = (p) => `${base}/api/${p}`;
const $    = (id) => document.getElementById(id);

// ---------- Icons (MDI) ----------
const modeIcon = (mode) => {
  const m = (mode || "").toLowerCase();
  if (m === "active") {
    return '<i class="mdi mdi-flash analysis-icon" aria-hidden="true"></i>';
  }
  return '<i class="mdi mdi-note-text-outline analysis-icon" aria-hidden="true"></i>';
};

// ---------- Utils ----------
const snippet = (text, max = 140) => {
  if (!text) return "";
  const t = String(text).trim().replace(/\s+/g, " ");
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
};

async function jsonFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${url}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

// ---------- Renderers ----------
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

    const btn = document.createElement("button");
    btn.className = "analysis-card";
    btn.innerHTML = `
      <div class="analysis-header">
        ${modeIcon(r.mode)}
        <div class="flex-1">
          <div class="flex items-center justify-between">
            <div class="font-semibold text-gray-100 capitalize">${r.mode ?? "passive"}</div>
            <div class="text-xs text-gray-300">${r.ts ?? ""}</div>
          </div>
          <div class="mt-1 text-sm text-gray-200/90">${snippet(r.summary)}</div>
          ${r.focus ? `
            <div class="mt-2 inline-flex items-center text-[11px] px-2 py-0.5 rounded bg-indigo-600/20 text-indigo-300">
              <i class="mdi mdi-crosshairs-gps mr-1"></i>${r.focus}
            </div>` : ""}
        </div>
      </div>
    `;
    btn.addEventListener("click", () => openModal(r));
    grid.appendChild(btn);
  });
}

// ---------- Data loaders ----------
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

// ---------- Interactions ----------
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

// ---------- Modal (with Markdown) ----------
function openModal(row) {
  const overlay = $("detailsOverlay");
  const title   = $("modalTitle");
  const meta    = $("modalMeta");
  const summary = $("modalSummary");

  title.innerHTML = `${modeIcon(row.mode)} <span class="capitalize">${row.mode ?? "passive"}</span> summary`;
  meta.textContent = [row.ts, row.focus ? `Focus: ${row.focus}` : ""].filter(Boolean).join(" • ");

  // Render Markdown -> HTML (marked loaded via CDN in index.html)
  try {
    summary.innerHTML = marked.parse(row.summary ?? "(No summary)");
  } catch {
    summary.textContent = row.summary ?? "(No summary)";
  }

  overlay.classList.remove("hidden");
  document.addEventListener("keydown", escClose);
  $("overlayBackdrop").addEventListener("click", closeModal, { once: true });
  $("modalClose").addEventListener("click", closeModal, { once: true });
}

function escClose(e) { if (e.key === "Escape") closeModal(); }
function closeModal() {
  $("detailsOverlay").classList.add("hidden");
  document.removeEventListener("keydown", escClose);
}

// ---------- Init ----------
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
