// Simple helper to construct API URLs and select DOM elements
const api = (p) => `api/${p}`;
const $ = (id) => document.getElementById(id);

// Render a history table row.  Rows may be dictionaries or arrays.
// We handle both to be robust to backend variations.
function renderRow(row) {
  const tr = document.createElement("tr");
  tr.className = "border-t border-gray-200";
  let ts, mode, summary;
  if (row && typeof row === "object" && !Array.isArray(row)) {
    ts = row.ts ?? "";
    mode = row.mode ?? "";
    summary = row.summary ?? "";
  } else if (Array.isArray(row)) {
    ts = row[1] ?? "";
    mode = row[2] ?? "";
    summary = row[4] ?? "";
  } else {
    ts = "";
    mode = "";
    summary = String(row ?? "");
  }
  tr.innerHTML = `
    <td class="p-2 whitespace-nowrap">${ts}</td>
    <td class="p-2 whitespace-nowrap">${mode}</td>
    <td class="p-2">${summary}</td>
  `;
  return tr;
}

// Fetch JSON via fetch API
async function jsonFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${url}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

// Render status information (event count and time since last run)
function renderStatus(data) {
  const count = data.event_count ?? 0;
  $("eventCount").textContent = `Events since last analysis: ${count}`;
  if (data.seconds_since_last != null) {
    const sec = Math.floor(data.seconds_since_last);
    const hours = Math.floor(sec / 3600);
    const minutes = Math.floor((sec % 3600) / 60);
    $("sinceLast").textContent = `Time since last analysis: ${hours}h ${minutes}m`;
  } else {
    $("sinceLast").textContent = "Time since last analysis: N/A";
  }
}

// Load status from backend and update mode button and status info
async function loadStatus() {
  const data = await jsonFetch(api("status"));
  $("toggleMode").textContent = data.mode || "passive";
  renderStatus(data);
}

// Load recent analyses and populate history table
async function loadHistory() {
  let rows = await jsonFetch(api("history"));
  if (!rows) rows = [];
  const dataRows = Array.isArray(rows) ? rows : Object.values(rows);
  const tbody = $("historyTable");
  tbody.innerHTML = "";
  dataRows.forEach((r) => tbody.appendChild(renderRow(r)));
}

// Toggle between active/passive modes
async function toggleMode() {
  const cur = $("toggleMode").textContent.trim().toLowerCase();
  const next = cur === "active" ? "passive" : "active";
  await jsonFetch(api(`mode?mode=${encodeURIComponent(next)}`), { method: "POST" });
  await loadStatus();
}

// Start a new analysis and handle progress bar updates
async function runAnalysisNow() {
  const mode = $("toggleMode").textContent.trim().toLowerCase() || "passive";
  const bar = $("progressBar");
  bar.style.width = "0%";
  requestAnimationFrame(() => {
    bar.style.width = "25%";
  });
  try {
    await jsonFetch(api("run"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    bar.style.width = "100%";
    // Always reload the full history from the server so that manual and automatic runs
    // both appear in the table.
    await loadHistory();
    await loadStatus();
  } catch (e) {
    console.error("runAnalysis failed:", e);
    bar.style.width = "100%";
    await loadHistory();
    await loadStatus();
  }
}

// Initialize event handlers and load initial data
function init() {
  $("toggleMode").addEventListener("click", toggleMode);
  $("runAnalysis").addEventListener("click", runAnalysisNow);
  loadStatus().catch(console.error);
  loadHistory().catch(console.error);
  // Periodically refresh the status and history without page reload.
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
