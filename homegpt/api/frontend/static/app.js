async function loadStatus() {
  try {
    const res = await fetch("api/status");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    document.getElementById("toggleMode").textContent = data.mode;
  } catch (e) {
    console.error("loadStatus failed:", e);
    document.getElementById("toggleMode").textContent = "error";
  }
}

async function toggleMode() {
  try {
    const btn = document.getElementById("toggleMode");
    const current = btn.textContent;
    const newMode = current === "passive" ? "active" : "passive";
    const res = await fetch(`api/mode?mode=${encodeURIComponent(newMode)}`, { method: "POST" });
    if (!res.ok) throw new Error(`status ${res.status}`);
    await loadStatus();
  } catch (e) {
    console.error("toggleMode failed:", e);
  }
}

async function loadHistory() {
  try {
    const res = await fetch("api/history");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    const table = document.getElementById("historyTable");
    table.innerHTML = "";
    data.forEach(row => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="p-2">${row[1]}</td><td class="p-2">${row[2]}</td><td class="p-2">${row[4]}</td>`;
      table.appendChild(tr);
    });
  } catch (e) {
    console.error("loadHistory failed:", e);
  }
}

document.getElementById("toggleMode").addEventListener("click", toggleMode);

document.getElementById("runAnalysis").addEventListener("click", async () => {
  try {
    const mode = document.getElementById("toggleMode").textContent || "passive";
    const res = await fetch("api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });
    if (!res.ok) throw new Error(`status ${res.status}`);
    await loadHistory();
  } catch (e) {
    console.error("runAnalysis failed:", e);
  }
});

loadStatus();
loadHistory();
