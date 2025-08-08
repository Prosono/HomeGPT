const api = (p) => `api/${p}`;
const $ = (id) => document.getElementById(id);

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

async function jsonFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${url}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

async function loadStatus() {
  const data = await jsonFetch(api("status"));
  $("toggleMode").textContent = data.mode || "passive";
}

async function loadHistory() {
  const rows = await jsonFetch(api("history")) || [];
  const tbody = $("historyTable");
  tbody.innerHTML = "";
  rows.forEach((r) => tbody.appendChild(renderRow(r)));
}

async function toggleMode() {
  const cur = $("toggleMode").textContent.trim().toLowerCase();
  const next = cur === "active" ? "passive" : "active";
  await jsonFetch(api(`mode?mode=${encodeURIComponent(next)}`), { method: "POST" });
  await loadStatus();
}

async function runAnalysisNow() {
  const mode = $("toggleMode").textContent.trim().toLowerCase() || "passive";
  try {
    const res = await jsonFetch(api("run"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });
    if (res && res.row) {
      // prepend newest row
      const tbody = $("historyTable");
      tbody.insertBefore(renderRow(res.row), tbody.firstChild);
    } else {
      await loadHistory();
    }
  } catch (e) {
    console.error("runAnalysis failed:", e);
    await loadHistory();
  }
}

function init() {
  $("toggleMode").addEventListener("click", toggleMode);
  $("runAnalysis").addEventListener("click", runAnalysisNow);
  loadStatus().catch(console.error);
  loadHistory().catch(console.error);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
