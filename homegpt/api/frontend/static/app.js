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

// ---------- Progress controller (robust + smooth) ----------
const prog = {
  raf: null,
  running: false,
  startTs: 0,
  value: 0,             // 0..100
  duration: 30000,      // ms to drift 25% -> 75% (tweak to taste)

  bar() { return document.getElementById("progressBar"); },

  set(v, immediate = false) {
    this.value = Math.max(0, Math.min(100, v));
    const el = this.bar();
    if (!el) return;
    if (immediate) {
      const prev = el.style.transition;
      el.style.transition = "none";
      el.style.width = this.value + "%";
      // force reflow to re-enable transitions
      void el.offsetWidth;
      el.style.transition = prev || "";
    } else {
      el.style.width = this.value + "%";
    }
  },

  

  startIdle() {
    // cancel any previous animation
    this.stop(false);
    this.running = true;
    this.startTs = performance.now();

    // kick to 25% immediately
    this.set(0, true);
    requestAnimationFrame(() => this.set(25));

    const easeOutQuad = (t) => t * (2 - t);

    const tick = (now) => {
      if (!this.running) return;
      const t = Math.min(1, (now - this.startTs) / this.duration);
      const eased = easeOutQuad(t);      // 0..1
      const target = 25 + 50 * eased;    // 25..75
      // only move forward, never backward
      if (this.value < target) this.set(Math.min(target, 75));
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  },

  finish() {
    // stop idle drift and animate to 100%
    this.stop(false);
    this.running = false;
    this.set(100); // let CSS animate the final jump
    // optional reset back to 0 after a moment
    setTimeout(() => this.set(0, true), 1200);
  },

  stop(reset = true) {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
    this.running = false;
    if (reset) this.set(0, true);
  }
};




// ---------- Utils ----------
const snippet = (text, max = 140) => {
  if (!text) return "";
  const t = String(text).trim().replace(/\s+/g, " ");
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
};


// Detects the first “summary” heading so we can render it as a hero
const isSummaryTitle = (txt = "") =>
  /summary/i.test(txt) && !/Energy|Security|Comfort|Anomal/i.test(txt);


// "time ago" helper
const timeAgo = (iso) => {
  if (!iso) return "";
  const then = new Date(iso);
  const sec = Math.max(1, (Date.now() - then.getTime()) / 1000);
  const m = Math.floor(sec/60), h = Math.floor(m/60);
  if (sec < 60) return `${Math.floor(sec)}s ago`;
  if (m < 60)   return `${m}m ago`;
  if (h < 24)   return `${h}h ago`;
  const d = Math.floor(h/24); return `${d}d ago`;
};

// ----- Canonical category mapping -----
const CANON = {
  security: "security",
  comfort: "comfort",
  energy: "energy",
  anomaly: "anomalies",
  anomalies: "anomalies",
  presence: "presence",
  occupancy: "presence",
  "estimated presence": "presence",
  "actions to take": "actions",
  "next steps": "actions",
  actions: "actions",
};

// Return a normalized category key for a heading string
function canonicalizeTitle(title = "") {
  const t = String(title).toLowerCase().trim();
  for (const key of Object.keys(CANON)) {
    if (t.includes(key)) return CANON[key];
  }
  return "generic";
}

function _escapeRe(s){return s.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");}

function coerceHeadings(md = "") {
  // Labels we want to treat as headings (case-insensitive)
  const labels = [
    "Passive summary","Summary","Details","Security","Comfort",
    "Energy","Anomalies","Presence","Occupancy","Actions to take","Actions","Next steps"
  ];
  const group = labels.map(_escapeRe).join("|");

  // Normalize newlines
  md = String(md).replace(/\r\n/g, "\n");

  // Convert lines that are *just* a label (optionally bold/with colon) into ### headings
  // Examples matched: "Security", "**Security**", "Actions to take:", "__Energy__  "
  const re = new RegExp(
    `^\\s*(?:\\*\\*|__)?\\s*(${group})\\s*(?:\\*\\*|__)?\\s*:?\\s*$`,
    "gmi"
  );
  md = md.replace(re, (_, lbl) => `### ${lbl}`);

  // Collapse excessive blank lines
  md = md.replace(/\n{3,}/g, "\n\n");
  return md;
}

function pillFor(title = "") {
  const c = canonicalizeTitle(title);
  switch (c) {
    case "security":  return { cls: "pill-sec",  icon: "<i class='mdi mdi-shield-lock-outline'></i>",   txt: "Security" };
    case "comfort":   return { cls: "pill-comf", icon: "<i class='mdi mdi-thermometer'></i>",           txt: "Comfort" };
    case "energy":    return { cls: "pill-ener", icon: "<i class='mdi mdi-flash-outline'></i>",         txt: "Energy" };
    case "anomalies": return { cls: "pill-ano",  icon: "<i class='mdi mdi-alert-circle-outline'></i>",  txt: "Anomalies" };
    case "presence":  return { cls: "pill-pres", icon: "<i class='mdi mdi-account-group-outline'></i>", txt: "Presence" };
    case "actions":   return { cls: "pill-reco", icon: "<i class='mdi mdi-lightbulb-on-outline'></i>",  txt: "Next steps" };
    default:          return null;
  }
}

function categoryIcon(title = "") {
  const c = canonicalizeTitle(title);
  switch (c) {
    case "security":  return '<i class="mdi mdi-shield-lock-outline"></i>';
    case "comfort":   return '<i class="mdi mdi-thermometer"></i>';
    case "energy":    return '<i class="mdi mdi-flash-outline"></i>';
    case "anomalies": return '<i class="mdi mdi-alert-circle-outline"></i>';
    case "presence":  return '<i class="mdi mdi-account-group-outline"></i>';
    case "actions":   return '<i class="mdi mdi-lightbulb-on-outline"></i>';
    default:          return '<i class="mdi mdi-subtitles-outline"></i>';
  }
}

function categoryClass(title = "") {
  const c = canonicalizeTitle(title);
  switch (c) {
    case "security":  return "theme-security";
    case "comfort":   return "theme-comfort";
    case "energy":    return "theme-energy";
    case "anomalies": return "theme-anomalies";
    case "presence":  return "theme-presence";
    case "actions":   return "theme-reco";
    default:          return "theme-generic";
  }
}

function escapeHtml(s=""){return s.replace(/[&<>"]/g,c=>({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));}

async function renderFeedbackListForEvent(eid) {
  const box = document.getElementById(`fb-list-${eid}`);
  if (!box) return;
  box.innerHTML = "<div class='text-gray-400 text-sm'>Loading…</div>";
  try {
    const rows = await jsonFetch(api(`feedback?event_id=${eid}&limit=50`)) || [];
    if (!rows.length) {
      box.innerHTML = "<div class='text-gray-400 text-sm'>No feedback yet.</div>";
      return;
    }
    box.innerHTML = rows.map(r => `
      <div class="fb-note py-1">
        <div class="text-xs text-gray-400">${new Date(r.ts).toLocaleString()} • ${escapeHtml(r.kind || "context")} • ${escapeHtml(r.source || "user")}</div>
        <div class="text-sm">${escapeHtml(r.note || "")}</div>
      </div>
    `).join("");
  } catch (e) {
    console.error("feedback load failed:", e);
    box.innerHTML = "<div class='text-red-400 text-sm'>Failed to load feedback.</div>";
  }
}

async function toggleFeedbackList(eid) {
  const box = document.getElementById(`fb-list-${eid}`);
  if (!box) return;
  const hidden = box.classList.contains("hidden");
  if (hidden && !box.dataset.loaded) {
    await renderFeedbackListForEvent(eid);
    box.dataset.loaded = "1";
  }
  box.classList.toggle("hidden");
}


// ==== History Analysis UI ====
(function () {
  // Elements
  const dlg = document.getElementById('dlg-history');
  const btn = document.getElementById('btn-analyze-history');
  const slot = document.getElementById('history-options');
  if (!dlg || !btn || !slot) return; // page not ready / ids changed

  const options = [1, 2, 4, 6, 10, 24];

  // Build option buttons
  slot.innerHTML = "";
  options.forEach(h => {
    const b = document.createElement('button');
    b.type = "button";
    b.className = "opt";
    b.textContent = `${h}h`;
    b.addEventListener('click', async () => {
      dlg.close();
      await runHistory(h);
    });
    slot.appendChild(b);
  });

  // Open dialog
  btn.addEventListener('click', () => dlg.showModal());

  // Optional helpers from your existing UI
  const progressBar = document.getElementById('progressBar');

  function setBusy(on) {
    if (progressBar) {
      progressBar.style.width = on ? "100%" : "0%";
      progressBar.style.transitionDuration = on ? "2000ms" : "500ms";
    }
    btn.disabled = !!on;
    btn.style.opacity = on ? 0.6 : 1;
    btn.style.pointerEvents = on ? "none" : "auto";
  }

  function showSummaryModal(title, meta, summary) {
    const overlay = document.getElementById('detailsOverlay');
    const titleEl = document.getElementById('modalTitle');
    const metaEl = document.getElementById('modalMeta');
    const sumEl = document.getElementById('modalSummary');
    const closeEl = document.getElementById('modalClose');

    if (!overlay || !titleEl || !metaEl || !sumEl) {
      alert(summary || '(no summary)');
      return;
    }
    titleEl.textContent = title;
    metaEl.textContent = meta || "";
    sumEl.textContent = summary || "";
    overlay.classList.remove('hidden');
    if (closeEl) closeEl.onclick = () => overlay.classList.add('hidden');
    const backdrop = document.getElementById('overlayBackdrop');
    if (backdrop) backdrop.onclick = () => overlay.classList.add('hidden');
  }

  async function runHistory(hours) {
    try {
      setBusy(true);
      // ✅ use api() so it works under HA Ingress or any base path
      const res = await fetch(api(`run_history?hours=${encodeURIComponent(hours)}`), {
        method: 'POST',
        headers: { 'Accept': 'application/json' }
      });

      const raw = await res.text();  // read text first
      let data = null;
      try {
        data = JSON.parse(raw);
      } catch (e) {
        console.error('Non-JSON response from /api/run_history:', raw);
        throw new Error(`Non-JSON response (${res.status}): ${raw.slice(0, 200)}`);
      }

      if (!res.ok) throw new Error(data?.message || `HTTP ${res.status}`);

      const when = data?.row?.ts || new Date().toISOString();
      showSummaryModal(
        `History analysis (${hours}h)`,
        `Run at ${when}`,
        data?.summary || '(no summary)'
      );
    } catch (e) {
      console.error(e);
      alert('History analysis failed: ' + e.message);
    } finally {
      setBusy(false);
    }
  }
})(); // ✅ close the IIFE
  


// Split markdown into sections grouped by headings (h1–h4)
function splitSections(markdown = "") {
  let tokens = [];
  try {
    const prepped = coerceHeadings(markdown);
    tokens = (window.marked && marked.lexer) ? marked.lexer(prepped) : [];
  } catch { tokens = []; }

  const sections = [];
  let cur = { title: null, bodyTokens: [] };

  const flush = () => {
    if (cur.title || cur.bodyTokens.length) sections.push(cur);
    cur = { title: null, bodyTokens: [] };
  };

  for (const tok of tokens) {
    if (tok.type === "heading" && tok.depth <= 4) {
      flush();
      cur.title = tok.text || "";
    } else {
      cur.bodyTokens.push(tok);
    }
  }
  flush();
  return sections;
}

function renderAnalysisTimeline(historyRows) {
  const container = document.getElementById('analysisTimeline');
  if (!container) return;
  container.innerHTML = "";

  historyRows.forEach(row => {
    const ts = row.ts || row[1];
    const summary = row.summary || row[4] || "";
    const categories = [];

    // Detect categories
    if (/^Security\s*-/im.test(summary)) categories.push({ name: "Security", class: "category-security" });
    if (/^Comfort\s*-/im.test(summary)) categories.push({ name: "Comfort", class: "category-comfort" });
    if (/^Energy\s*-/im.test(summary)) categories.push({ name: "Energy", class: "category-energy" });
    if (/^Anomalies\s*-/im.test(summary)) categories.push({ name: "Anomalies", class: "category-anomalies" });

    // Create entry
    const entry = document.createElement('div');
    entry.className = 'timeline-entry';
    entry.innerHTML = `
      <div class="timestamp">${new Date(ts).toLocaleString()}</div>
      <div class="categories">
        ${categories.map(c => `<span class="category-tag ${c.class}">${c.name}</span>`).join("")}
      </div>
      <div class="details">${summary.replace(/\n/g, '<br>')}</div>
    `;

    // Expand/collapse on click
    entry.addEventListener('click', () => {
      const details = entry.querySelector('.details');
      details.style.display = details.style.display === 'block' ? 'none' : 'block';
    });

    container.appendChild(entry);
  });
}


// Extract the first “kWh” and “W” value from a summary string
function extractMetrics(summary = "") {
  let energy = null, power = null;
  const kwhMatch = summary.match(/([\d.]+)\s*kWh/i);
  if (kwhMatch) energy = parseFloat(kwhMatch[1]);
  const wattMatch = summary.match(/([\d.]+)\s*W\b/i);
  if (wattMatch) power = parseFloat(wattMatch[1]);
  return { energy, power };
}

// --- scoring helpers ---
function scoreSection(bodyTokens, catKey) {
  // base: list items + long paragraphs
  let score = 0;
  for (const t of (bodyTokens || [])) {
    if (t.type === "list") score += Math.min(3, t.items?.length || 0);
    if (t.type === "paragraph") {
      const len = (t.text || "").trim().length;
      if (len > 60) score += 1;
      if (len > 180) score += 1;
    }
  }
  // keyword weighting per category (tunable)
  const txt = (bodyTokens || []).map(t => t.text || "").join(" ").toLowerCase();
  const add = (arr, w=1) => arr.forEach(k => { if (txt.includes(k)) score += w; });

  if (catKey === "security") {
    add(["unlocked","open","door","window","garage","alarm"], 2);
    add(["unknown","not_home","detected"], 1);
  } else if (catKey === "comfort") {
    add(["cold","hot","draft","open window","heating"], 1);
  } else if (catKey === "energy") {
    add(["w","kw","kwh","price","cheapest","peak","limit"], 1);
  } else if (catKey === "anomalies") {
    add(["unavailable","error","stuck","offline","failed"], 2);
  }
  return score;
}


function makeContinuousHours(endDate, hoursBack) {
  const hours = [];
  // align end to current hour (e.g., 10:00, 11:00, …)
  const end = new Date(endDate);
  end.setMinutes(0,0,0);
  for (let i = hoursBack - 1; i >= 0; i--) {
    const d = new Date(end.getTime() - i * 3600 * 1000);
    hours.push(d.toISOString());
  }
  return hours; // strictly increasing ISO hours
}

// Return map: { hourIso: { security: n, comfort: n, energy: n, anomalies: n }, meta: {hourIso:[rows]} }
function buildHeatmapData(rows, hoursBack = 24) {
  const cats = ["security","comfort","energy","anomalies"];
  const end = Date.now();
  const hours = makeContinuousHours(end, hoursBack);

  // init matrices
  const buckets = {};
  const meta = {};
  hours.forEach(h => {
    buckets[h] = { security:0, comfort:0, energy:0, anomalies:0 };
    meta[h] = [];
  });

  // drop rows outside range
  const startTs = new Date(hours[0]).getTime();
  const endTs   = new Date(hours[hours.length - 1]).getTime() + 3599999;

  rows.forEach(r => {
    const row = Array.isArray(r) ? { ts: r[1], summary: r[4] } : r;
    const ts = new Date(row.ts || Date.now()).getTime();
    if (ts < startTs || ts > endTs) return;

    // hour bucket index
    const hAligned = new Date(ts); hAligned.setMinutes(0,0,0);
    const hourIso = hAligned.toISOString();
    meta[hourIso].push(row);

    const sections = splitSections(row.summary || "");
    for (const sec of sections) {
      const key = canonicalizeTitle(sec.title || "");
      if (!cats.includes(key)) continue;
      buckets[hourIso][key] += scoreSection(sec.bodyTokens, key);
    }
  });

  return { hours, buckets, meta };
}


function cellColor(v, vmax) {
  if (!vmax) return "#0f172a";
  const t = Math.max(0, Math.min(1, v / vmax));
  const hue = 220 - 180 * Math.pow(t, 0.8);     // blue → amber
  const light = 18 + 42 * Math.pow(t, 0.6);
  return `hsl(${hue} 85% ${light}%)`;
}

function renderHeatmap(rows, { range=24, filter="all", threshold=0 } = {}) {
  const wrap = document.getElementById("analysisHeatmap");
  if (!wrap) return;

  const { hours, buckets, meta } = buildHeatmapData(rows, range);
  const cats = [
    { key: "security",  label: "Security"  },
    { key: "comfort",   label: "Comfort"   },
    { key: "energy",    label: "Energy"    },
    { key: "anomalies", label: "Anomalies" }
  ];

  // vmax per category for color scaling
  const vmax = {};
  cats.forEach(c => {
    let m = 0;
    hours.forEach(h => { m = Math.max(m, buckets[h]?.[c.key] || 0); });
    vmax[c.key] = Math.max(1, m);
  });

  // grid: sticky label + N hour columns
  wrap.style.gridTemplateColumns = `max-content repeat(${hours.length}, 22px)`;
  wrap.innerHTML = "";

  cats.forEach(c => {
    if (filter !== "all" && filter !== c.key) return;

    // label column
    const label = document.createElement("div");
    label.textContent = c.label;
    label.className = "hm-label";
    wrap.appendChild(label);

    // hour cells
    hours.forEach(h => {
      const v = buckets[h]?.[c.key] || 0;
      if (v < threshold) {
        const cell = document.createElement("div");
        cell.className = "hm-cell";
        cell.style.background = "#0f172a";
        wrap.appendChild(cell);
        return;
      }

      const cell = document.createElement("div");
      cell.className = "hm-cell";
      cell.style.background = cellColor(v, vmax[c.key]);

      // tooltip with first bullet/paragraph snippet for that category
      const tip = document.createElement("div");
      tip.className = "hm-tooltip";
      tip.textContent = `${new Date(h).toLocaleString()} • ${c.label}: ${v}`;
      cell.appendChild(tip);

      // hover → highlight matching cards
      cell.addEventListener("mouseenter", () => {
        const rowsAtHour = meta[h] || [];
        highlightCards(rowsAtHour, c.key);
      });
      cell.addEventListener("mouseleave", () => {
        highlightCards([], c.key);
      });

      // click → open best analysis for that hour/category
      cell.addEventListener("click", () => {
        const candidates = meta[h] || [];
        let best = null, bestScore = -1;
        for (const row of candidates) {
          const sections = splitSections(row.summary || "");
          for (const sec of sections) {
            if (canonicalizeTitle(sec.title || "") === c.key) {
              const s = scoreSection(sec.bodyTokens, c.key);
              if (s > bestScore) { bestScore = s; best = row; }
            }
          }
        }
        if (best) openModal(best);
      });

      wrap.appendChild(cell);
    });
  });
}

// Visually emphasize preview cards that match the hour/category rows
function highlightCards(rowsAtHour, catKey) {
  const ids = new Set((rowsAtHour || []).map(r => (Array.isArray(r) ? r[0] : r.id)));
  document.querySelectorAll(".preview-card").forEach(card => {
    const title = card.querySelector(".preview-title")?.textContent?.toLowerCase() || "";
    const matches = ids.size === 0 ? false : true; // you can match by row id if you inject it on card
    card.classList.toggle("ring-1", matches);
    card.classList.toggle("ring-blue-300/50", matches);
  });
}

// hook up filter buttons
function initHeatmapFilters(rows) {
  document.querySelectorAll('[data-hm-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      const f = btn.getAttribute('data-hm-filter');
      renderHeatmap(rows, f);
    });
  });
}



async function loadHistory() {
  let rows = await jsonFetch(api("history"));
  if (!rows) rows = [];
  const dataRows = Array.isArray(rows) ? rows : Object.values(rows);
  
  renderAnalysisTimeline(dataRows);
  renderGrid(dataRows); // existing cards

  let HM_STATE = { range: 24, filter: "all", threshold: 0 };

  renderHeatmap(dataRows, HM_STATE);

  // filter chips
  document.querySelectorAll('[data-hm-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      HM_STATE.filter = btn.getAttribute('data-hm-filter');
      renderHeatmap(dataRows, HM_STATE);
    });
  });

  // range chips
  document.querySelectorAll('[data-hm-range]').forEach(btn => {
    btn.addEventListener('click', () => {
      HM_STATE.range = parseInt(btn.getAttribute('data-hm-range'), 10) || 24;
      renderHeatmap(dataRows, HM_STATE);
    });
  });

  // severity threshold slider
  const thr = document.getElementById('hm-threshold');
  const thrVal = document.getElementById('hm-threshold-val');
  if (thr && thrVal) {
    thr.addEventListener('input', () => {
      HM_STATE.threshold = parseInt(thr.value, 10) || 0;
      thrVal.textContent = String(HM_STATE.threshold);
      renderHeatmap(dataRows, HM_STATE);
    });
  }


  initHeatmapFilters(dataRows);

  

  // --- NEW: Build data for the chart ---
  const { labels, categoryCounts, categoryDetails } = buildCategoryChartData(dataRows);
  const energyPts = [];
  const powerPts  = [];
  dataRows.forEach(row => {
    const ts = row.ts || row[1];
    const summary = row.summary || row[4];
    const { energy, power } = extractMetrics(summary);
    labels.push(new Date(ts).toLocaleString());
    energyPts.push(energy);
    powerPts.push(power);
  });

  renderTrendChart(labels, energyPts, powerPts);

  renderCategoryTrendChart(labels, categoryCounts, categoryDetails);
}

function renderCategoryTrendChart(labels, counts, details) {
  const el = document.getElementById('categoryChart');
  if (!el) return;
  const ctx = el.getContext('2d');

  if (window._categoryChart) {
    window._categoryChart.destroy();
  }

  window._categoryChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Security Events',
          data: counts.Security,
          borderColor: '#ef4444',
          backgroundColor: 'rgba(239,68,68,0.2)',
          fill: false,
          tension: 0.3
        },
        {
          label: 'Comfort Issues',
          data: counts.Comfort,
          borderColor: '#f59e0b',
          backgroundColor: 'rgba(245,158,11,0.2)',
          fill: false,
          tension: 0.3
        },
        {
          label: 'Energy Alerts',
          data: counts.Energy,
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.2)',
          fill: false,
          tension: 0.3
        },
        {
          label: 'Anomalies',
          data: counts.Anomalies,
          borderColor: '#8b5cf6',
          backgroundColor: 'rgba(139,92,246,0.2)',
          fill: false,
          tension: 0.3
        }
      ]
    },
    options: {
      responsive: true,
      interaction: { mode: 'nearest', intersect: true },
      plugins: {
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const cat = ctx.dataset.label.replace(/ Events| Issues| Alerts/g, "");
              const detail = details[cat][ctx.dataIndex];
              return detail ? `${ctx.dataset.label}: ${detail}` : `${ctx.dataset.label}: None`;
            }
          }
        }
      },
      onClick: (evt, elements) => {
        if (elements.length > 0) {
          const { datasetIndex, index } = elements[0];
          const cat = window._categoryChart.data.datasets[datasetIndex].label.replace(/ Events| Issues| Alerts/g, "");
          const detail = details[cat][index];
          if (detail) {
            alert(`${cat} event at ${labels[index]}:\n${detail}`);
            // Optionally: scroll to that card in your UI
          }
        }
      },
      scales: {
        y: { title: { display: true, text: "Event Count" }, beginAtZero: true },
        x: { title: { display: true, text: "Time" } }
      }
    }
  });
}


function buildCategoryChartData(historyRows) {
  const labels = [];
  const categoryCounts = {
    Security: [],
    Comfort: [],
    Energy: [],
    Anomalies: []
  };
  const categoryDetails = {
    Security: [],
    Comfort: [],
    Energy: [],
    Anomalies: []
  };

  historyRows.forEach(row => {
    const ts = row.ts || row[1];
    const summary = row.summary || row[4] || "";
    labels.push(new Date(ts).toLocaleString());

    // For each category, find mentions in the summary
    ["Security", "Comfort", "Energy", "Anomalies"].forEach(cat => {
      const regex = new RegExp(`^${cat} - (.+)`, "im");
      const match = summary.match(regex);
      if (match) {
        categoryCounts[cat].push(1);
        categoryDetails[cat].push(match[1]);
      } else {
        categoryCounts[cat].push(0);
        categoryDetails[cat].push(null);
      }
    });
  });

  return { labels, categoryCounts, categoryDetails };
}


function renderTrendChart(labels, energyData, powerData) {
  const el = document.getElementById('analysisChart');
  if (!el) return;
  const ctx = el.getContext('2d');

  // Use a different global to store the Chart.js instance
  if (window._analysisChart) {
    window._analysisChart.destroy();
  }

  window._analysisChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Hourly energy usage (kWh)',
          data: energyData,
          borderColor: '#34d399',
          backgroundColor: 'transparent',
          tension: 0.3,
          spanGaps: true
        },
        {
          label: 'Current power draw (W)',
          data: powerData,
          borderColor: '#60a5fa',
          backgroundColor: 'transparent',
          tension: 0.3,
          spanGaps: true,
          yAxisID: 'y2'
        }
      ]
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: { tooltip: { enabled: true }, legend: { position: 'top' } },
      scales: {
        y: {
          type: 'linear',
          display: true,
          position: 'left',
          title: { display: true, text: 'kWh' },
          beginAtZero: true
        },
        y2: {
          type: 'linear',
          display: true,
          position: 'right',
          title: { display: true, text: 'W' },
          grid: { drawOnChartArea: false }
        },
        x: { title: { display: true, text: 'Analysis timestamp' } }
      }
    }
  });
}

// --- FEEDBACK DIALOG PLUMBING ---
function openFeedbackDialog({ analysis_id, category = "generic", body = "", event_id = null, presetNote = "" } = {}) {
  const dlg = $("dlg-feedback");
  if (!dlg) return;

  $("fb-analysis-id").value = analysis_id || "";
  $("fb-event-id").value    = event_id || "";
  $("fb-body").value        = body || "";
  $("fb-kind").value        = "context";
  $("fb-category").value    = (category || "generic").toLowerCase();
  $("fb-text").value        = presetNote;

  // Context preview for the user
  $("fb-context").textContent = body ? `About: ${body.slice(0, 280)}${body.length > 280 ? "…" : ""}` : "";

  $("fb-result").textContent = "";
  dlg.showModal();
}

(function initFeedbackDialog(){
  const dlg = $("dlg-feedback");
  if (!dlg) return;

  $("fb-cancel")?.addEventListener("click", () => dlg.close());

  $("fb-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submitBtn = $("fb-submit");
    submitBtn.disabled = true;

    // Build payload (server can resolve event by analysis_id + body if event_id omitted)
    const payload = {
      analysis_id: $("fb-analysis-id").value ? Number($("fb-analysis-id").value) : undefined,
      event_id: $("fb-event-id").value ? Number($("fb-event-id").value) : undefined,
      category: $("fb-category").value || "generic",
      kind: $("fb-kind").value || "context",
      note: ($("fb-text").value || "").trim(),
      body: $("fb-body").value || ""
    };

    if (!payload.note) {
      $("fb-result").textContent = "Please add a short note before submitting.";
      submitBtn.disabled = false;
      return;
    }

    try {
      // Align with your backend route name:
    await jsonFetch(api("feedback"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)  // <-- use the form payload you built above
    });
      $("fb-result").textContent = "Thanks — saved!";
      await loadEvents();     
      if ($("fb-event-id").value) {
        const eid = Number($("fb-event-id").value);
        const box = document.getElementById(`fb-list-${eid}`);
        if (box && !box.classList.contains("hidden")) {
          box.dataset.loaded = "";                            // force reload
          await renderFeedbackListForEvent(eid);
        }
      }
      setTimeout(()=> dlg.close(), 600);
    } catch (err) {
      console.error("Feedback error:", err);
      $("fb-result").textContent = "Sorry, failed to save feedback.";
    } finally {
      submitBtn.disabled = false;
    }
  });
})();


// Build preview data: pills, first points, and a numeric series (sparkline)
function parsePreview(summary = "") {
  const sections = splitSections(summary);

  // headings in order
  const headings = sections
    .map(s => s.title)
    .filter(Boolean);

  // extract first 2 points from the first section that has lists/paragraphs
  let points = [];
  for (const sec of sections) {
    if (sec.bodyTokens && sec.bodyTokens.length) {
      for (const t of sec.bodyTokens) {
        if (t.type === "list") {
          for (const it of t.items) {
            const txt = (window.marked?.parseInline?.(it.text || "") || "")
              .replace(/<[^>]+>/g, "").trim();
            if (txt) points.push(txt);
            if (points.length >= 2) break;
          }
        } else if (t.type === "paragraph") {
          const txt = (window.marked?.parseInline?.(t.text || "") || "")
            .replace(/<[^>]+>/g, "").trim();
          if (txt && txt.length > 24) points.push(txt);
        }
        if (points.length >= 2) break;
      }
    }
    if (points.length >= 2) break;
  }

  // numeric series for sparkline (scan full text)
  const plain = String(summary).replace(/`[^`]+`/g, "");
  const nums = (plain.match(/-?\d+(?:\.\d+)?/g) || [])
    .map(parseFloat)
    .filter(n => !isNaN(n));

  let series = null;
  if (nums.length >= 4) {
    const take = Math.min(20, nums.length);
    const step = Math.floor(nums.length / take) || 1;
    series = nums.filter((_, i) => i % step === 0).slice(0, take);
  }

  // build pills from headings
  const pills = [];
  for (const h of headings) {
    const p = pillFor(h);
    if (p && !pills.find(x => x.txt === p.txt)) pills.push(p);
    if (pills.length >= 4) break;
  }

  return { pills, points, series };
}


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

async function renderGrid(rows) {
  const grid = $("analysisGrid");
  grid.innerHTML = "";

  rows.forEach((row) => {
    const r = Array.isArray(row)
      ? { id: row[0], ts: row[1], mode: row[2], focus: row[3], summary: row[4], actions: row[5] }
      : row;

    const { pills, points, series } = parsePreview(r.summary || "");

    const card = document.createElement("button");
    card.className = "preview-card w-full text-left hover:bg-white/5 transition-colors";
    card.addEventListener("click", () => openModal(r));

    // header
    const modePretty = (r.mode||"passive").charAt(0).toUpperCase() + (r.mode||"passive").slice(1);
    card.innerHTML = `
      <div class="preview-head">
        <i class="mdi ${r.mode==='active' ? 'mdi-flash-outline text-emerald-300' : 'mdi-note-text-outline text-indigo-300'} text-xl"></i>
        <div class="preview-title">${modePretty}</div>
        <div class="preview-time" title="${r.ts || ''}">${timeAgo(r.ts)}</div>
      </div>
      <div class="preview-pills"></div>
      <div class="preview-body">
        <div class="preview-points">
          ${points.length ? points.slice(0,2).map(p=>`<div class="point">• ${p}</div>`).join("") : `<div class="point">• ${snippet(r.summary, 120)}</div>`}
          ${r.focus ? `<div class="focus-chip"><i class="mdi mdi-crosshairs-gps"></i> ${r.focus}</div>` : ""}
        </div>
        <div class="preview-spark">${series ? `<canvas></canvas>` : ""}</div>
      </div>
    `;

    // pills
    const pillsWrap = card.querySelector(".preview-pills");
    if (pills.length) {
      pills.forEach(p => {
        const span = document.createElement("span");
        span.className = `pill ${p.cls}`;
        span.innerHTML = `${p.icon}${p.txt}`;
        pillsWrap.appendChild(span);
      });
    }

    // sparkline
    if (series && series.length) {
      try {
        const canvas = card.querySelector("canvas");
        const ctx = canvas.getContext("2d");
        new Chart(ctx, {
          type: "line",
          data: { labels: series.map((_,i)=>i+1), datasets: [{ data: series, tension: .35, pointRadius: 0, borderWidth: 2 }] },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display:false } },
            scales: { x: { display:false }, y: { display:false } }
          }
        });
        canvas.style.width = "100%";
        canvas.style.height = "44px";
      } catch (e) { console.warn("sparkline failed:", e); }
    }

    grid.appendChild(card);
  });
}

// ---------- Data loaders ----------
async function loadStatus() {
  const data = await jsonFetch(api("status"));
  $("toggleMode").textContent = data.mode || "passive";
  renderStatus(data);
}

//async function loadHistory() {
//  let rows = await jsonFetch(api("history"));
//  if (!rows) rows = [];
//  const dataRows = Array.isArray(rows) ? rows : Object.values(rows);
//  renderGrid(dataRows);
//}

// ---------- Interactions ----------
async function toggleMode() {
  const cur = $("toggleMode").textContent.trim().toLowerCase();
  const next = cur === "active" ? "passive" : "active";
  await jsonFetch(api(`mode?mode=${encodeURIComponent(next)}`), { method: "POST" });
  await loadStatus();
}

async function loadEvents() {
  const since = new Date(Date.now() - 24*3600*1000).toISOString();
  const rows = await jsonFetch(api(`events?since=${encodeURIComponent(since)}&limit=200`)) || [];
  const box = document.getElementById("eventsList");
  if (!box) return;

  box.innerHTML = rows.map(ev => `
    <div class="py-2 flex flex-col gap-2 border-b border-white/5">
      <div class="flex items-start gap-3">
        <div class="min-w-[10ch] text-gray-400">${new Date(ev.ts).toLocaleString()}</div>
        <span class="chip">${ev.category}</span>
        <div class="flex-1">
          <div class="font-medium">${escapeHtml(ev.title || 'Event')}</div>
          <div class="text-gray-400">${escapeHtml(ev.body || '')}</div>
        </div>
      </div>
      <div class="flex gap-2 pl-[10ch]">
        <button class="chip" onclick="openFeedbackDialog({analysis_id:${ev.analysis_id}, event_id:${ev.id}, category:'${ev.category}', body:${JSON.stringify(ev.body || '')}})">
          Add feedback
        </button>
        <button class="chip" onclick="toggleFeedbackList(${ev.id})">
          View feedback (${ev.feedback_count || 0})
        </button>
      </div>
      <div id="fb-list-${ev.id}" class="hidden mt-1 pl-[10ch]"></div>
    </div>
  `).join("");
}


// ---------- Click handler using the controller ----------
async function runAnalysisNow() {
  const btn = document.getElementById("runAnalysis");
  if (btn.dataset.busy === "1") return; // guard double-clicks
  btn.dataset.busy = "1";
  btn.setAttribute("aria-busy", "true");
  btn.classList.add("opacity-60", "pointer-events-none");

  const mode = document.getElementById("toggleMode").textContent.trim().toLowerCase() || "passive";

  // start progress: 25% immediately, then drift toward 75%
  prog.startIdle();

  try {
    await jsonFetch(api("run"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });
    // snap to 100% as soon as we have a response
    prog.finish();
    await loadHistory();
    await loadStatus();
  } catch (e) {
    console.error("runAnalysis failed:", e);
    // still complete the bar so UI doesn't get stuck
    prog.finish();
  } finally {
    btn.dataset.busy = "0";
    btn.removeAttribute("aria-busy");
    btn.classList.remove("opacity-60", "pointer-events-none");
  }
}

// ---------- Modal (with Markdown) ----------
async function openModal(row) {
  const overlay   = $("detailsOverlay");
  const title     = $("modalTitle");
  const meta      = $("modalMeta");
  const container = $("modalSummary");

  // --- Title + meta ---
  title.innerHTML = `${modeIcon(row.mode)} <span class="capitalize">${row.mode ?? "passive"}</span> summary`;
  meta.textContent = [row.ts, row.focus ? `Focus: ${row.focus}` : ""].filter(Boolean).join(" • ");

  const raw = row.summary ?? "(No summary)";

  // --- normalize bare labels into Markdown headings ---
  const _escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const coerceHeadings = (md = "") => {
    const labels = [
      "Passive summary","Summary","Details","Security","Comfort",
      "Energy","Anomalies","Presence","Occupancy","Actions to take","Actions","Next steps"
    ];
    const group = labels.map(_escRe).join("|");
    md = String(md).replace(/\r\n/g, "\n");
    const re = new RegExp(String.raw`^\s*(?:\*\*|__)?\s*(${group})\s*(?:\*\*|__)?\s*:?\s*$`, "gmi");
    md = md.replace(re, (_m, lbl) => `### ${lbl}`);
    return md.replace(/\n{3,}/g, "\n\n");
  };

  // --- Followups ---
  const followups = await jsonFetch(api(`followups?analysis_id=${row.id}`)) || [];
  if (followups.length) {
    const actionsWrap = document.createElement("div");
    actionsWrap.className = "followup-actions flex gap-2 mt-2";
    followups.forEach(f => {
      const b = document.createElement("button");
      b.className = "chip";
      b.textContent = f.label;
      b.addEventListener("click", async () => {
        b.disabled = true;
        const data = await jsonFetch(api("followup/run"), {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({ analysis_id: row.id, code: f.code })
        });
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(data.payload, null, 2);
        document.getElementById("modalSummary").appendChild(pre);
      });
      actionsWrap.appendChild(b);
    });
    container.appendChild(actionsWrap);
  }

  // --- Preprocess + tokenize ---
  const prepped = coerceHeadings(raw);
  let tokens = [];
  try { tokens = (window.marked && marked.lexer) ? marked.lexer(prepped) : []; }
  catch { container.textContent = raw; return; }

  // --- Fetch events for feedback mapping ---
  const events = await jsonFetch(api(`events?since=${row.ts}&category=`)) || [];
  const eventMap = {};
  events.forEach(ev => {
    const key = (ev.body || "").trim();
    eventMap[key] = ev.id;
  });

  // --- Group tokens by headings ---
  const sections = [];
  let current = { title: null, bodyTokens: [] };
  const flush = () => {
    if (current.title || current.bodyTokens.length) sections.push(current);
    current = { title: null, bodyTokens: [] };
  };
  for (const tok of tokens) {
    if (tok.type === "heading" && tok.depth <= 4) { flush(); current.title = tok.text || ""; }
    else { current.bodyTokens.push(tok); }
  }
  flush();

  // --- Build UI ---
  container.innerHTML = "";

  // Hero summary (first section if "Summary")
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
      <div class="hero-head">${heroIcon}<span>${heroTitle}</span></div>
      <div class="hero-body">${heroHtml}</div>
    `;
    container.appendChild(hero);
    sections.shift();
  }

  // Masonry wrap
  const wrap = document.createElement("div");
  wrap.className = "modal-masonry";
  container.appendChild(wrap);

  // Section cards
  sections.forEach((sec, idx) => {
    const t = sec.title || (idx === 0 ? "Details" : `Section ${idx + 1}`);
    const theme = categoryClass(t);

    const card = document.createElement("div");
    card.className = `modal-section ${theme}`;

    // Heading
    const h = document.createElement("h3");
    h.innerHTML = `${categoryIcon(t)} ${t}`;
    card.appendChild(h);

    // Body
    const body = document.createElement("div");
    body.className = "section-body";
    let html = "";
    try { html = marked.parser(sec.bodyTokens); }
    catch { html = `<p>${raw}</p>`; }
    body.innerHTML = html;
    card.appendChild(body);

    // Optional chart if numbers present
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
            scales: { x: { display: false }, y: { ticks: { color: "#e5e7eb" }, grid: { color: "rgba(255,255,255,0.10)" } } }
          }
        });
        canvas.style.height = "120px";
      } catch (e) { console.warn("Chart render failed:", e); }
    }

    // ----- Inline feedback controls (modal) -----
    const eligible = new Set(["Comfort","Security","Energy","Anomalies","Presence"]);

    // tiny helpers
    const norm = (s="") => String(s).replace(/\s+/g," ").trim();
    const esc  = window.escapeHtml || ((s="") => s.replace(/[&<>"]/g,c=>({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c])));

    // Build mapping: body -> event (for this analysis only)
    let evByBody = {};
    try {
      const evRows = await jsonFetch(api(`events?since=${encodeURIComponent(row.ts)}&limit=1000`)) || [];
      evRows
        .filter(e => e.analysis_id === row.id)
        .forEach(e => { evByBody[norm(e.body || "")] = e; });
    } catch { /* non-fatal */ }

    // Load all feedback for this analysis and group by body
    const fbByBody = new Map();
    try {
      const fbs = await jsonFetch(api(`feedback?analysis_id=${row.id}&limit=1000`)) || [];
      fbs.forEach(f => {
        const k = norm(f.body || "");
        if (!fbByBody.has(k)) fbByBody.set(k, []);
        fbByBody.get(k).push(f);
      });
    } catch { /* non-fatal */ }

    function renderFbList(listEl, items) {
      if (!items || !items.length) {
        listEl.innerHTML = "<div class='text-gray-400 text-sm'>No feedback yet.</div>";
        return;
      }
      listEl.innerHTML = items.map(r => `
        <div class="fb-note py-1 border-t border-white/5">
          <div class="text-xs text-gray-400">
            ${new Date(r.ts || Date.now()).toLocaleString()} • ${esc(r.kind || "context")}
          </div>
          <div class="text-sm">${esc(r.note || "")}</div>
        </div>
      `).join("");
    }

    if (eligible.has(t)) {
      const addControlsFor = (el) => {
        const rawText = el.textContent || "";
        const key = norm(rawText);
        if (!key) return;

        const ev  = evByBody[key];                       // may be undefined (ok)
        const arr = fbByBody.get(key) || [];             // existing notes for this bullet
        const count = arr.length;

        const box = document.createElement("div");
        box.className = "feedback-box mt-2";

        box.innerHTML = `
          <div class="flex gap-2 items-start">
            <textarea class="feedback-text flex-1" rows="2" placeholder="Add feedback about this item…"></textarea>
            <button class="feedback-save chip">Save</button>
            <button class="feedback-toggle chip">View (${count})</button>
          </div>
          <div class="feedback-list hidden mt-2"></div>
        `;

        // Save handler — backend can resolve event by (analysis_id + body)
        box.querySelector(".feedback-save").addEventListener("click", async () => {
          const txtEl = box.querySelector(".feedback-text");
          const note = (txtEl.value || "").trim();
          if (!note) return;

          try {
            await jsonFetch(api("feedback"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                event_id: ev?.id,                     // undefined is fine
                analysis_id: row.id,
                category: canonicalizeTitle(t),       // e.g. "security"
                body: key,                             // exact bullet text
                note,                                  // the user's note
                kind: "context"
              })
            });
            // optimistic UI update
            const now = new Date().toISOString();
            const updated = [{ ts: now, note, kind: "context", body: key }, ...(fbByBody.get(key) || [])];
            fbByBody.set(key, updated);
            txtEl.value = "";

            const toggleBtn = box.querySelector(".feedback-toggle");
            if (toggleBtn) toggleBtn.textContent = `View (${updated.length})`;

            const listEl = box.querySelector(".feedback-list");
            if (listEl && !listEl.classList.contains("hidden")) {
              renderFbList(listEl, updated);
            }
          } catch (e) {
            console.error("Save feedback failed:", e);
            // (optional) show a small error message here
          }
        });

        // View toggle
        const listEl = box.querySelector(".feedback-list");
        box.querySelector(".feedback-toggle").addEventListener("click", async () => {
          listEl.classList.toggle("hidden");
          if (!listEl.classList.contains("hidden")) {
            const items = fbByBody.get(key) || [];
            renderFbList(listEl, items);
          }
        });

        el.appendChild(box);
      };

      // Attach to bullet items + paragraphs
      card.querySelectorAll("li, p").forEach(addControlsFor);
    }

    wrap.appendChild(card);
  });

  // Finalize modal open
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
// ---------- Init ----------
function init() {
  // wire up buttons
  $("toggleMode").addEventListener("click", toggleMode);
  $("runAnalysis").addEventListener("click", runAnalysisNow);

  // initial loads
  loadStatus().catch(console.error);
  loadHistory().catch(console.error);
  loadEvents().catch(console.error);   // 👈 new – load events into UI

  // poll periodically
  setInterval(() => {
    loadStatus().catch(console.error);
    loadHistory().catch(console.error);
    loadEvents().catch(console.error); // 👈 keep events fresh
  }, 100000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
