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

// Category → icon (MDI)
const categoryIcon = (title = "") => {
  const t = title.toLowerCase();
  if (/\bsecurity\b/.test(t))                       return '<i class="mdi mdi-shield-lock-outline"></i>';
  if (/\bcomfort\b/.test(t))                        return '<i class="mdi mdi-thermometer"></i>';
  if (/\benergy\b/.test(t))                         return '<i class="mdi mdi-flash-outline"></i>';
  if (/\banomal(y|ies)\b/.test(t))                  return '<i class="mdi mdi-alert-circle-outline"></i>';
  if (/estimated\s+presence|occupancy/i.test(t))    return '<i class="mdi mdi-account-group-outline"></i>';
  if (/recommendations|next steps/i.test(t))        return '<i class="mdi mdi-lightbulb-on-outline"></i>';
  return '<i class="mdi mdi-subtitles-outline"></i>';
};

// Category → theme class
const categoryClass = (title = "") => {
  const t = title.toLowerCase();
  if (/\bsecurity\b/.test(t))                       return "theme-security";
  if (/\bcomfort\b/.test(t))                        return "theme-comfort";
  if (/\benergy\b/.test(t))                         return "theme-energy";
  if (/\banomal(y|ies)\b/.test(t))                  return "theme-anomalies";
  if (/estimated\s+presence|occupancy/i.test(t))    return "theme-presence";
  if (/recommendations|next steps/i.test(t))        return "theme-reco";
  return "theme-generic";
};

// Detects the first “summary” heading so we can render it as a hero
const isSummaryTitle = (txt = "") =>
  /summary/i.test(txt) && !/energy|security|comfort|anomal/i.test(txt);


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
  const overlay   = $("detailsOverlay");
  const title     = $("modalTitle");
  const meta      = $("modalMeta");
  const container = $("modalSummary");

  title.innerHTML = `${modeIcon(row.mode)} <span class="capitalize">${row.mode ?? "passive"}</span> summary`;
  meta.textContent = [row.ts, row.focus ? `Focus: ${row.focus}` : ""].filter(Boolean).join(" • ");

  const raw = row.summary ?? "(No summary)";
  let tokens = [];
  try { tokens = marked.lexer(raw); } catch { container.textContent = raw; }

  // Group tokens by headings (h1–h4)
  const sections = [];
  let current = { title: null, bodyTokens: [] };
  const flush = () => {
    if (current.title || current.bodyTokens.length) sections.push(current);
    current = { title: null, bodyTokens: [] };
  };
  for (const tok of tokens) {
    if (tok.type === "heading" && tok.depth <= 4) { flush(); current.title = tok.text; }
    else { current.bodyTokens.push(tok); }
  }
  flush();

  // Build: hero (first “Summary …” section) + masonry for the rest
  container.innerHTML = "";

  // 1) Hero banner (optional)
  const first = sections[0];
  if (first && isSummaryTitle(first.title || "")) {
    const hero = document.createElement("div");
    hero.className = "modal-hero";
    const heroIcon = '<i class="mdi mdi-home-analytics-outline"></i>';
    const heroTitle = first.title || "Summary";
    let heroHtml = "";
    try { heroHtml = marked.parser(first.bodyTokens); }
    catch { heroHtml = `<p>${raw}</p>`; }

    hero.innerHTML = `
      <div class="hero-head">
        ${heroIcon}
        <span>${heroTitle}</span>
      </div>
      <div class="hero-body">${heroHtml}</div>
    `;
    container.appendChild(hero);
    sections.shift(); // remove from list; remaining go to masonry
  }

  // 2) Masonry wrap
  const wrap = document.createElement("div");
  wrap.className = "modal-masonry"; // CSS columns → variable height cards
  container.appendChild(wrap);

  sections.forEach((sec, idx) => {
    const t = sec.title || (idx === 0 ? "Details" : `Section ${idx + 1}`);
    const theme = categoryClass(t);

    const card = document.createElement("div");
    card.className = `modal-section ${theme}`;

    const h = document.createElement("h3");
    h.innerHTML = `${categoryIcon(t)} ${t}`;
    card.appendChild(h);

    const body = document.createElement("div");
    body.className = "section-body";
    let html = "";
    try { html = marked.parser(sec.bodyTokens); }
    catch { html = `<p>${raw}</p>`; }
    body.innerHTML = html;
    card.appendChild(body);

    // Numbers → tiny line chart
    const plain = body.textContent || "";
    const nums = (plain.match(/-?\d+(?:\.\d+)?/g) || []).map(parseFloat).filter(n => !isNaN(n));
    if (nums.length >= 3) {
      const unit =
        plain.includes("°C") ? "°C" :
        plain.includes("kWh") ? "kWh" :
        plain.includes("kW")  ? "kW"  :
        /Mb\/?s|Mbps/i.test(plain) ? "Mbps" : "";

      const chartBox = document.createElement("div");
      chartBox.className = "section-chart";
      const canvas = document.createElement("canvas");
      chartBox.appendChild(canvas);
      card.appendChild(chartBox);

      try {
        const labels = nums.map((_, i) => `${i + 1}`);
        new Chart(canvas.getContext("2d"), {
          type: "line",
          data: { labels, datasets: [{ data: nums, label: unit || "Values", tension: 0.35, pointRadius: 0, borderWidth: 2 }] },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: !!unit } },
            scales: {
              x: { display: false },
              y: { ticks: { color: "#e5e7eb" }, grid: { color: "rgba(255,255,255,0.10)" } }
            }
          }
        });
        canvas.style.height = "120px";
      } catch (e) {
        console.warn("Chart render failed:", e);
      }
    }

    wrap.appendChild(card);
  });

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
