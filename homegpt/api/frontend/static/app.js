// ---- tiny helpers ----
const api = (path) => `api/${path}`;
const $  = (id) => document.getElementById(id);

async function jsonFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} at ${url}\n${text}`);
  }
  // some endpoints might return empty; guard it
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

// ---- UI wiring ----
async function loadStatus() {
  try {
    const data = await jsonFetch(api("status"));
    $("toggleMode").textContent = data.mode || "passive";
  } catch (e) {
    console.error("loadStatus:", e);
    $("toggleMode").textContent = "error";
  }
}

async function toggleMode() {
  try {
    const cur = $("toggleMode").textContent.trim().toLowerCase();
    const next = cur === "active" ? "passive" : "active";
    await jsonFetch(api(`mode?mode=${encodeURIComponent(next)}`), { method: "POST" });
    await loadStatus();
    await loadHistory();
  } catch (e) {
    console.error("toggleMode:", e);
  }
}

async function runAnalysisNow() {
  try {
    const mode = $("toggleMode").textContent.trim().toLowerCase() || "passive";
    await jsonFetch(api("run"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });
    await loadHistory();
  } catch (e) {
    console.error("runAnalysis:", e);
  }
}

function renderHistory(rows) {
  const tbody = $("historyTable");
  tbody.innerHTML = "";
  rows.forEach((row) => {
    // expected schema from db.get_analyses(): [id, ts, mode, focus, summary, actions_json]
    const ts      = row[1] ?? "";
    const mode    = row[2] ?? "";
    const summary = row[4] ?? "";
    const tr = document.createElement("tr");
    tr.className = "border-t border-gray-200";
    tr.innerHTML = `
      <td class="p-2 whitespace-nowrap">${ts}</td>
      <td class="p-2 whitespace-nowrap">${mode}</td>
      <td class="p-2">${summary}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadHistory() {
  try {
    const rows = await jsonFetch(api("history"));
    renderHistory(rows || []);
  } catch (e) {
    console.error("loadHistory:", e);
  }
}

// ---- bootstrap ----
function init() {
  $("toggleMode").addEventListener("click", toggleMode);
  $("runAnalysis").addEventListener("click", runAnalysisNow);
  loadStatus();
  loadHistory();
}

// If you included `defer` on the script tag, DOM is ready already
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
