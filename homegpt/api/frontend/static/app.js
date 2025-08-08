async function loadStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();
  document.getElementById("toggleMode").textContent = data.mode;
}

async function toggleMode() {
  const current = document.getElementById("toggleMode").textContent;
  const newMode = current === "passive" ? "active" : "passive";
  await fetch(`/api/mode?mode=${newMode}`, { method: "POST" });
  loadStatus();
}

async function loadHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  const table = document.getElementById("historyTable");
  table.innerHTML = "";
  data.forEach(row => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="p-2">${row[1]}</td><td class="p-2">${row[2]}</td><td class="p-2">${row[4]}</td>`;
    table.appendChild(tr);
  });
}

document.getElementById("toggleMode").addEventListener("click", toggleMode);
document.getElementById("runAnalysis").addEventListener("click", async () => {
  await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: document.getElementById("toggleMode").textContent }) });
  loadHistory();
});

loadStatus();
loadHistory();
