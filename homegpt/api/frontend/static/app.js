// Simple helper to construct API URLs and select DOM elements
const api = (p) => `api/${p}`;
const $ = (id) => document.getElementById(id);

// Render a history table row from a database row.  DB rows are returned
// as arrays: [id, timestamp, mode, focus, summary, actions].
function renderRow(row) {
  const tr = document.createElement("tr");
  tr.className = "border-t border-gray-200";
  const ts = row[1] ?? "";
  const mode = row[2] ?? "";
  const summary = row[4] ?? "";
  tr.innerHTML = `
    <td class="p-2 whitespace-nowrap">${ts}</td>
    <td class="p-2 whitespace-nowrap">${mode}</td>
    <td class="p-2">${summary}</td>
  `;
  return tr;
}

// Fetch JSON via fetch API and ensure we only return JSON data
async function jsonFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${url}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

// Render status information (event count and time since last run)
function renderStatus(data) {
  // Event count
  const count = data.event_count ?? 0;
  $("eventCount").textContent = `Events since last analysis: ${count}`;
  // Time since last analysis in human‑readable form
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
  const rows = await jsonFetch(api("history")) || [];
  const tbody = $("historyTable");
  tbody.innerHTML = "";
  rows.forEach((r) => tbody.appendChild(renderRow(r)));
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
  // Reset and start progress bar
  const bar = $("progressBar");
  bar.style.width = "0%";
  // Give the browser a chance to paint before updating to 25%
  requestAnimationFrame(() => {
    bar.style.width = "25%";
  });
  try {
    const res = await jsonFetch(api("run"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    // Mark progress as complete
    bar.style.width = "100%";
    if (res && res.row) {
      // Prepend newest row to history
      const tbody = $("historyTable");
      tbody.insertBefore(renderRow(res.row), tbody.firstChild);
    } else {
      await loadHistory();
    }
    // Reload status after analysis completes
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
  // Periodically refresh the status (event count and time since last analysis)
  // without reloading the page.  Adjust the interval (ms) as needed.
  setInterval(() => {
    loadStatus().catch(console.error);
  }, 10000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentDOMContentLoaded", init);
} else {
  init();
}
