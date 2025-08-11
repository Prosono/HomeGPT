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
  try { tokens = (window.marked && marked.lexer) ? marked.lexer(markdown) : []; }
  catch { tokens = []; }

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
