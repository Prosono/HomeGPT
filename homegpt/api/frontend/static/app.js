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


const CAT_WORDS = ["security","comfort","energy","anomalies","presence","actions"];

const _clean = s => (s||"").trim();
const _isCatWord = s => CAT_WORDS.includes(_clean(s).toLowerCase());
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
  
// ---- HA deep-link builders (ingress safe) ----
// ---- HA deep-link builders (ingress safe)
function haBasePath() {
  const p = location.pathname, k = "/api/hassio_ingress/";
  const i = p.indexOf(k);
  return i >= 0 ? p.slice(0, i) || "/" : "/";
}
function joinUrl(a, b){ return a.replace(/\/+$/,"") + "/" + String(b||"").replace(/^\/+/,""); }

const HA = {
  url: (p="") => joinUrl(location.origin + haBasePath(), p),

  // stable deep links
  entityHistory: (eid) => HA.url(`/history?entity_id=${encodeURIComponent(eid)}`),
  entityManage:  (eid) => HA.url(`/config/entities/entity/${encodeURIComponent(eid)}`),
  entityStates:  (eid) => HA.url(`/developer-tools/state?entity_id=${encodeURIComponent(eid)}`),
  deviceManage:  (did) => HA.url(`/config/devices/device/${encodeURIComponent(did)}`)
};

// sensible default for clicking the in-text entity mention:
function defaultEntityHref(eid) {
  const domain = String(eid).split(".")[0];
  return ["sensor","binary_sensor","climate","input_number","number","utility_meter"]
    .includes(domain)
    ? HA.entityHistory(eid)   // sensors → History
    : HA.entityManage(eid);   // others → Entity editor
}
// ---- Linkifier
function linkifyEntities(text, { entity_ids = [], device_ids = [], addChips = true, alreadyEscaped = false } = {}) {
  if (!text) return "";
  const toArr = (v) => Array.isArray(v) ? v
    : (typeof v === "string" ? v.split(/[,\s]+/).map(s => s.trim()).filter(Boolean) : []);
  const eids = [...new Set(toArr(entity_ids))];
  const dids = [...new Set(toArr(device_ids))];

  let html = alreadyEscaped ? String(text) : escapeHtml(String(text));

  html = html.replace(/\b([a-z_]+)\.([\w:-]+)\b/g, (m) =>
    `<a class="entity-link" data-entity-id="${m}" href="${defaultEntityHref(m)}" target="_blank" rel="noopener">${m}</a>`
  );

  if (addChips) {
    const chips = [];

    if (eids.length) {
      chips.push(`<a class="chip entity-chip" href="${HA.entityManage(eids[0])}" target="_blank" rel="noopener">✏️ Edit</a>`);
    }

    if (dids.length) {
      chips.push(`<a class="chip entity-chip" href="${HA.deviceManage(dids[0])}" target="_blank" rel="noopener">🔧 Device</a>`);
    } else if (eids.length) {
      // No device_id known → add a placeholder that resolveDeviceChips() will replace
      chips.push(`<span class="chip entity-chip device-chip-loader" data-entity-id="${eids[0]}">🔧 Device</span>`);
    }

    if (chips.length) html += `<div class="entity-chip-row mt-1">${chips.join(" ")}</div>`;
  }
  return html;
}

// --- Resolve device_id from an entity_id via HA WebSocket ---
function getHass() {
  try {
    if (window.hass) return window.hass;
    const host = document.querySelector("home-assistant") 
              || window.parent?.document?.querySelector("home-assistant");
    return host?.hass || null;
  } catch { return null; }
}

async function ensureEntityRegistry() {
  if (window._entityRegistryList) return window._entityRegistryList;
  const hass = getHass();
  if (!hass?.connection) return null;
  try {
    window._entityRegistryList = await hass.connection.sendMessagePromise({
      type: "config/entity_registry/list"
    });
    return window._entityRegistryList;
  } catch (e) {
    console.warn("entity_registry/list failed", e);
    return null;
  }
}

async function resolveDeviceChips(root = document) {
  const reg = await ensureEntityRegistry();
  if (!reg) return;
  root.querySelectorAll(".device-chip-loader[data-entity-id]").forEach(node => {
    const eid = node.getAttribute("data-entity-id");
    const entry = reg.find(e => e.entity_id === eid);
    const did = entry?.device_id;
    if (!did) { node.remove(); return; }

    const a = document.createElement("a");
    a.className = "chip entity-chip";
    a.href = HA.deviceManage(did);
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "🔧 Device";
    node.replaceWith(a);
  });
}

// Run linkify inside already-rendered HTML (only text nodes between tags)
function linkifyHtml(html="") {
  return String(html).replace(/>([^<]+)</g, (_, txt) => ">" + linkifyEntities(txt) + "<");
}

function isNoiseEvent(ev){
  const t = _clean(ev.title), b = _clean(ev.body);
  const dup = t && b && t.toLowerCase() === b.toLowerCase();
  const bothShort = (t+b).length < 6;
  // junk if title is just the category and body is blank OR also just the category
  if (_isCatWord(t) && (!b || _isCatWord(b))) return true;
  // junk if nothing meaningful
  if ((!t && !b) || bothShort) return true;
  // “Comfort/Comfort” style
  if (dup && _isCatWord(t)) return true;
  return false;
}

function makeNiceTitle(ev){
  const t = _clean(ev.title), b = _clean(ev.body);
  if (!t || _isCatWord(t) || t.toLowerCase() === b.toLowerCase()) {
    // first clause/sentence from body
    const m = b.match(/^(.{0,110}?)(?:[.!?]|—| - |:|$)/);
    return (m ? m[1] : b || "Event").replace(/\s+/g," ").replace(/\.$/,"");
  }
  return t;
}

function formatBody(ev){
  let b = _clean(ev.body);
  if (!b || b.toLowerCase() === _clean(ev.title).toLowerCase() || _isCatWord(b)) {
    // fall back to something a touch richer
    b = _clean(ev.body) || "(no extra details)";
  }
  // highlight a few key numbers/units
  b = escapeHtml(b).replace(
    /(-?\d+(?:\.\d+)?)\s?(kWh|kW|W|°C|%|NOK\/kWh)/g,
    "<b class='num'>$1&nbsp;$2</b>"
  );
  // turn entity ids into chips (best-effort)
  const ents = (_clean(ev.entities) ? ev.entities.split(",") :
               (ev.body||"").match(/\b(?:sensor|binary_sensor|switch|climate|lock|light|media_player)\.[\w_:-]+\b/g) || [])
               .slice(0,6);
  const chips = ents.length ? `<div class="mt-1">${ents.map(e=>`<span class="chip">${escapeHtml(e)}</span>`).join(" ")}</div>` : "";
  return b + chips;
}

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

function ensureOverlayVisible() {
  const ov = $("detailsOverlay");
  if (!ov) return false;
  if (ov.parentNode !== document.body) document.body.appendChild(ov);
  ov.classList.remove("hidden");
  ov.style.position = "fixed";
  ov.style.inset = "0";
  ov.style.zIndex = "99999";
  ov.style.overflow = "hidden";     // ← belt & suspenders
  return true;
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

function layoutModalScroll() {
  const body = $("modalSummary");
  if (!body) return;

  // This element will scroll
  body.style.overflowY = "auto";
  body.style.overscrollBehavior = "contain";
  body.style.minHeight = "0";            // IMPORTANT when a parent is flex
  // Fit from its current top to the bottom of the viewport with a small margin
  const top = body.getBoundingClientRect().top;
  const max = Math.max(120, window.innerHeight - top - 24);
  body.style.maxHeight = max + "px";

  // Prevent inner wrappers creating their own scrollbars
  const masonry = body.querySelector(".modal-masonry");
  if (masonry) masonry.style.overflow = "visible";
  body.querySelectorAll(".modal-section,.section-body").forEach(el => {
    el.style.overflow = "visible";
  });
}
window.addEventListener("resize", layoutModalScroll);


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

// --- FEEDBACK DIALOG PLUMBING (robust) ---
function openFeedbackDialog({
  analysis_id,
  category = "generic",
  body = "",
  event_id = null,
  presetNote = ""
} = {}) {
  const dlg = $("dlg-feedback");
  if (!dlg) return;

  const set = (id, val) => { const el = $(id); if (el) el.value = val ?? ""; };

  set("fb-analysis-id", analysis_id ?? "");
  set("fb-event-id",    event_id ?? "");
  set("fb-body",        body);
  set("fb-kind",        "context");
  set("fb-category",    (category || "generic").toLowerCase());
  set("fb-text",        presetNote);

  const ctx = $("fb-context");
  if (ctx) ctx.textContent = body
    ? `About: ${body.slice(0, 280)}${body.length > 280 ? "…" : ""}`
    : "";

  const res = $("fb-result");
  if (res) res.textContent = "";

  // open <dialog> or fallback
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.classList.remove("hidden");
}

(function initFeedbackDialog(){
  const dlg = $("dlg-feedback");
  if (!dlg) return;

  $("fb-cancel")?.addEventListener("click", () => {
    dlg.close?.();
    dlg.classList.add("hidden");
  });

  $("fb-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const resEl = $("fb-result");
    const submitBtn = $("fb-submit");
    if (submitBtn) submitBtn.disabled = true;

    // Safely read values (avoid null .value accesses)
    const getV = id => ($(id) && $(id).value) || "";
    const analysisIdNum = Number(getV("fb-analysis-id"));
    const eventIdNum    = Number(getV("fb-event-id"));

    const payload = {
      analysis_id: Number.isFinite(analysisIdNum) && analysisIdNum > 0 ? analysisIdNum : undefined,
      event_id:    Number.isFinite(eventIdNum)    && eventIdNum > 0    ? eventIdNum    : undefined,
      category: (getV("fb-category") || "generic").toLowerCase(),
      kind:     (getV("fb-kind") || "context").toLowerCase(),
      note:     (getV("fb-text") || "").trim(),
      body:     (getV("fb-body") || "").trim()
    };

    if (!payload.note) {
      if (resEl) resEl.textContent = "Please add a short note before submitting.";
      submitBtn && (submitBtn.disabled = false);
      return;
    }

    try {
      const res = await fetch(api("feedback"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      // read text first (backend might return plain text on error)
      const raw = await res.text();
      let data = null;
      try { data = JSON.parse(raw); } catch {}

      if (!res.ok) {
        const msg = data?.detail || data?.message || raw || `HTTP ${res.status}`;
        throw new Error(msg);
      }

      if (resEl) resEl.textContent = "Thanks — saved!";

      // refresh sidebar events list (if present)
      if (typeof loadEvents === "function") {
        await loadEvents();
      }

      // if we saved against a specific event and its list is open, refresh that list
      const eid = payload.event_id;
      if (eid && typeof renderFeedbackListForEvent === "function") {
        const box = document.getElementById(`fb-list-${eid}`);
        if (box && !box.classList.contains("hidden")) {
          box.dataset.loaded = "";           // force reload
          await renderFeedbackListForEvent(eid);
        }
      }

      setTimeout(() => { dlg.close?.(); dlg.classList.add("hidden"); }, 500);

    } catch (err) {
      console.error("Feedback error:", err);
      if (resEl) resEl.textContent = "Sorry, failed to save feedback.";
    } finally {
      submitBtn && (submitBtn.disabled = false);
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
    card.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        const p = openModal(r);
        if (p && typeof p.then === "function") {
          p.catch(err => {
            console.error("openModal (async) failed:", err);
            alert("Couldn't open the analysis view. See console for details.");
          });
        }
      } catch (err) {
        console.error("openModal failed:", err);
        alert("Couldn't open the analysis view. See console for details.");
      }
    });

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

// --- helpers for pills + paging
const cap = s => (s||"").charAt(0).toUpperCase() + (s||"").slice(1);
function pillClassForCategory(cat="") {
  const key = canonicalizeTitle(cat); // you already have this
  switch(key){
    case "security":  return "pill pill-sec";
    case "comfort":   return "pill pill-comf";
    case "energy":    return "pill pill-ener";
    case "anomalies": return "pill pill-ano";
    case "presence":  return "pill pill-pres";
    case "actions":   return "pill pill-reco";
    default:          return "pill";
  }
}

let EV_CACHE = [];
let EV_PAGE = 1;
const EV_PAGE_SIZE = 15;

// Helper: map category → pill class (matches your analysis colors)
function eventPillClass(cat = "") {
  switch ((cat || "").toLowerCase()) {
    case "security":  return "pill pill-sec";
    case "comfort":   return "pill pill-comf";
    case "energy":    return "pill pill-ener";
    case "anomalies": return "pill pill-ano";
    case "presence":  return "pill pill-pres";
    case "actions":   return "pill pill-reco";
    default:          return "pill"; // fallback
  }
}

// SAFE: no inline JS; all listeners attached programmatically.
async function loadEvents() {
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();

  let rows = [];
  try {
    rows = (await jsonFetch(api(`events?since=${encodeURIComponent(since)}&limit=400`))) || [];
  } catch (e) {
    console.error("loadEvents: fetch failed", e);
  }

  const listEl = $("eventsList");
  if (!listEl) return;

  rows = rows
    .filter(ev => !isNoiseEvent(ev))
    .map(ev => ({
      ...ev,
      _titleHtml: linkifyEntities(makeNiceTitle(ev), { addChips: false }),
      _bodyHtml:  linkifyEntities(ev.body || "", { entity_ids: ev.entity_ids, device_ids: ev.device_ids, addChips: true })
    }));
  // Keep a cache for paging
  EV_CACHE = rows;

  // Ensure there's a pager element after the list
  let pagerEl = $("eventsPager");
  if (!pagerEl) {
    pagerEl = document.createElement("div");
    pagerEl.id = "eventsPager";
    listEl.insertAdjacentElement("afterend", pagerEl);
  }

  // Render just the visible slice
  const paintEventsList = (slice) => {
    listEl.innerHTML = "";

    if (!slice.length) {
      listEl.innerHTML = `<div class="text-sm text-gray-400 py-3">No events in the last 24h.</div>`;
      return;
    }

    slice.forEach(ev => {
      const row = document.createElement("div");
      row.className = "event-row";

      const ts = ev.ts ? new Date(ev.ts).toLocaleString() : "";

      row.innerHTML = `
        <div class="event-meta">
          <div class="event-time">${escapeHtml(ts)}</div>
          <span class="${eventPillClass(ev.category)}">${escapeHtml(ev.category || "generic")}</span>
        </div>

        <div class="event-main">
          <div class="title">${ev._titleHtml || "Event"}</div>
          <div class="body">${ev._bodyHtml || ""}</div>
        </div>

        <div class="event-actions"> … </div>
        <div id="fb-list-${ev.id}" class="hidden mt-1 col-span-3"></div>
      `;

      // Wire buttons
      row.querySelector(".js-add")?.addEventListener("click", () => {
        openFeedbackDialog({
          analysis_id: ev.analysis_id,
          event_id: ev.id,
          category: (ev.category || "generic"),
          body: ev.body || ""
        });
      });

      row.querySelector(".js-view")?.addEventListener("click", () => {
        toggleFeedbackList(ev.id);
      });

      resolveDeviceChips();
      listEl.appendChild(row);
    });
  };

  const renderPager = ({ total, page, pages, startIndex }) => {
    if (pages <= 1) { pagerEl.innerHTML = ""; return; }
    const end = Math.min(total, startIndex + EV_PAGE_SIZE);

    pagerEl.innerHTML = `
      <div class="flex items-center justify-center gap-2 py-3">
        <button class="pager-btn" data-page="first" ${page === 1 ? "disabled" : ""} title="First">&laquo;</button>
        <button class="pager-btn" data-page="prev"  ${page === 1 ? "disabled" : ""} title="Previous">&lsaquo;</button>
        <span class="pager-meta">Showing ${startIndex + 1}&ndash;${end} of ${total} • Page ${page}/${pages}</span>
        <button class="pager-btn" data-page="next"  ${page === pages ? "disabled" : ""} title="Next">&rsaquo;</button>
        <button class="pager-btn" data-page="last"  ${page === pages ? "disabled" : ""} title="Last">&raquo;</button>
      </div>
    `;

    // Replace any previous handler
    pagerEl.onclick = (ev) => {
      const btn = ev.target.closest(".pager-btn");
      if (!btn) return;

      const maxPages = Math.max(1, Math.ceil(EV_CACHE.length / EV_PAGE_SIZE));
      if (btn.dataset.page === "first") goto(1);
      else if (btn.dataset.page === "prev") goto(EV_PAGE - 1);
      else if (btn.dataset.page === "next") goto(EV_PAGE + 1);
      else if (btn.dataset.page === "last") goto(maxPages);
    };
  };

  const goto = (page) => {
    const total = EV_CACHE.length;
    const pages = Math.max(1, Math.ceil(total / EV_PAGE_SIZE));
    EV_PAGE = Math.min(Math.max(1, page), pages);

    const start = (EV_PAGE - 1) * EV_PAGE_SIZE;
    const slice = EV_CACHE.slice(start, start + EV_PAGE_SIZE);

    paintEventsList(slice);
    renderPager({ total, page: EV_PAGE, pages, startIndex: start });
  };

  // Initial render
  goto(EV_PAGE || 1);
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

document.addEventListener('click', (e) => {
  const btn = e.target.closest('.btn-toggle .chip');
  if (!btn) return;

  const group = btn.closest('.btn-toggle');

  // Single-select groups (range) -> only one true
  if (btn.dataset.hmRange) {
    group.querySelectorAll('.chip').forEach(c => c.setAttribute('aria-pressed', 'false'));
    btn.setAttribute('aria-pressed', 'true');
    return;
  }

  // Multi-select groups (categories), except "All"
  if (btn.dataset.hmFilter === 'all') {
    group.querySelectorAll('.chip').forEach(c => c.setAttribute('aria-pressed','false'));
    btn.setAttribute('aria-pressed','true');
  } else {
    // toggle pressed
    const pressed = btn.getAttribute('aria-pressed') === 'true';
    btn.setAttribute('aria-pressed', pressed ? 'false' : 'true');
    // ensure "All" turns off when any specific filter is on
    const allBtn = group.querySelector('[data-hm-filter="all"]');
    if (allBtn) allBtn.setAttribute('aria-pressed','false');
  }
});

// SPECTRA ASK

async function askSpectra(q) {
  const box = $("askResult");
  if (!box) return;
  box.classList.remove("hidden");
  box.innerHTML = `<div class="text-sm text-gray-400">Thinking…</div>`;

  try {
    const res = await fetch(api("ask"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q })
    });
    const data = await res.json();

    const md = window.marked?.parse(data.answer_md || "") || escapeHtml(data.answer_md || "");
    let html = `<div class="ask-answer">${md}</div>`;

    if (data.automation_yaml) {
      html += `
        <div class="mt-3">
          <div class="row"><span class="text-sm text-gray-400">Automation YAML</span>
            <button class="chip" id="copyYaml">Copy</button>
          </div>
          <pre><code>${escapeHtml(data.automation_yaml)}</code></pre>
        </div>`;
    }
    if (Array.isArray(data.links) && data.links.length) {
      html += `<div class="ask-links">${data.links.map(l =>
        `<a class="chip" target="_blank" rel="noopener" href="${HA.url(l.url || l.href || "/")}">${escapeHtml(l.label || "Open")}</a>`
      ).join(" ")}</div>`;
    }

    // turn inline entity_ids into links + add chips (uses your linkifier)
    box.innerHTML = linkifyHtml(html);

    // copy button
    box.querySelector("#copyYaml")?.addEventListener("click", () => {
      navigator.clipboard.writeText(data.automation_yaml || "");
    });

  } catch (e) {
    console.error(e);
    box.innerHTML = `<div class="text-red-400">Ask failed: ${e.message}</div>`;
  }
}

$("askSend")?.addEventListener("click", () => {
  const q = $("askInput").value.trim();
  if (q) askSpectra(q);
});
$("askInput")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const q = e.target.value.trim();
    if (q) askSpectra(q);
  }
});

// SPECTRA ASK END

// ---------- Modal (with Markdown) ----------
async function openModal(row) {
  const overlay   = $("detailsOverlay");
  const title     = $("modalTitle");
  const meta      = $("modalMeta");
  const container = $("modalSummary");

  if (!overlay || !title || !meta || !container) {
    console.error("Modal DOM missing.", { overlay: !!overlay, title: !!title, meta: !!meta, container: !!container });
    alert(snippet(row?.summary || "(no summary available)", 800));
    return;
  }


  // 🔴 show the overlay *immediately* and put a visible placeholder
  ensureOverlayVisible();

  // one-time Esc handler stored on the overlay instance
  if (!overlay._escHandler) {
    overlay._escHandler = (e) => {
      if (e.key === "Escape") closeModal();
    };
  }
  document.addEventListener("keydown", overlay._escHandler);


  
  // (re)bind close buttons each time we open
  const bindClose = () => closeModal();
  $("overlayBackdrop")?.addEventListener("click", bindClose, { once: true });
  $("modalClose")?.addEventListener("click", bindClose, { once: true });

  title.innerHTML = `${modeIcon(row.mode)} <span class="capitalize">${row.mode ?? "passive"}</span> summary`;
  meta.textContent = [row.ts, row.focus ? `Focus: ${row.focus}` : ""].filter(Boolean).join(" • ");

  // give the user instant feedback while we parse/await
  container.innerHTML = `
    <div class="modal-hero">
      <div class="hero-head">
        <i class="mdi mdi-home-analytics-outline"></i><span>Loading…</span>
      </div>
      <div class="hero-body">
        Preparing view for this analysis. One moment…
      </div>
    </div>
  `;

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

  const isSummaryTitle = (txt = "") =>
    /summary/i.test(txt) && !/Energy|Security|Comfort|Anomal/i.test(txt);

  const norm = (s="") => String(s).replace(/\s+/g," ").trim();
  const esc  = window.escapeHtml || ((s="") => s.replace(/[&<>"]/g,c=>({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c])));

  // --- Preprocess + tokenize (never early-return) ---
  const prepped = coerceHeadings(raw);
  let tokens = [];
  try {
    tokens = (window.marked && marked.lexer) ? marked.lexer(prepped) : [];
  } catch (e) {
    console.warn("marked.lexer failed; falling back to a single paragraph", e);
    tokens = [{ type: "paragraph", text: raw }];
  }

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
  if (!sections.length) sections.push({ title: "Details", bodyTokens: [{ type: "paragraph", text: raw }] });

  // --- Prefetch once for this analysis (wrapped; won't throw) ---
  let evByBody = {};
  const bodyByEventId = {};
  try {
    if (row.ts && row.id != null) {
      const evRows = await jsonFetch(api(`events?since=${encodeURIComponent(row.ts)}&limit=1000`)) || [];
      evRows
        .filter(e => e.analysis_id === row.id)
        .forEach(e => {
          const k = norm(e.body || "");
          evByBody[k] = e;
          if (e.id != null) bodyByEventId[e.id] = k;
        });
    }
  } catch (e) { console.warn("events prefetch failed", e); }

  const fbByBody = new Map();
  try {
    const fbs = await jsonFetch(api(`feedback?analysis_id=${row.id}&limit=1000`)) || [];
    fbs.forEach(f => {
      const key = norm(f.body || bodyByEventId[f.event_id] || "");
      if (!key) return;
      if (!fbByBody.has(key)) fbByBody.set(key, []);
      fbByBody.get(key).push(f);
    });
  } catch (e) { console.warn("feedback prefetch failed", e); }

  // --- Followups (safe) ---
  try {
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
          try {
            const data = await jsonFetch(api("followup/run"), {
              method: "POST",
              headers: {"Content-Type":"application/json"},
              body: JSON.stringify({ analysis_id: row.id, code: f.code })
            });
            const pre = document.createElement("pre");
            pre.className = "followup-response";
            pre.textContent = JSON.stringify(data.payload, null, 2);
            container.appendChild(pre);
          } finally { b.disabled = false; }
        });
        actionsWrap.appendChild(b);
      });
      // We'll append this after the hero below to keep structure neat
      container._followups = actionsWrap;
    }
  } catch (e) { console.warn("followups fetch failed", e); }

  // --- Build UI ---
  container.innerHTML = "";

  // Hero summary
  const first = sections[0];
  if (first && isSummaryTitle(first.title || "")) {
    const hero = document.createElement("div");
    hero.className = "modal-hero";
    const heroIcon = '<i class="mdi mdi-home-analytics-outline"></i>';
    const heroTitle = first.title || "Summary";
    let heroHtml = "";
    try { heroHtml = marked.parser(first.bodyTokens); }
    catch { heroHtml = `<p>${esc(raw)}</p>`; }
    hero.innerHTML = `
      <div class="hero-head">${heroIcon}<span>${heroTitle}</span></div>
      <div class="hero-body">${heroHtml}</div>
    `;
    container.appendChild(hero);
    sections.shift();
  }

  // If we fetched followups, place them now under the hero
  if (container._followups) container.appendChild(container._followups);

  // Masonry wrap
  const wrap = document.createElement("div");
  wrap.className = "modal-masonry";
  container.appendChild(wrap);

  // Helper to render a feedback list
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

  const eligible = new Set(["Comfort","Security","Energy","Anomalies","Presence"]);

  for (const [idx, sec] of sections.entries()) {
    const t = sec.title || (idx === 0 ? "Details" : `Section ${idx + 1}`);
    const theme = categoryClass(t);

    const card = document.createElement("div");
    card.className = `modal-section ${theme}`;

    // Heading
    const h = document.createElement("h3");
    h.innerHTML = `${categoryIcon(t)} ${esc(t)}`;
    card.appendChild(h);

    // Body
    const body = document.createElement("div");
    body.className = "section-body";
    let html = "";
    try { html = marked.parser(sec.bodyTokens); }
    catch { html = `<p>${esc(raw)}</p>`; }
    body.innerHTML = html;
    card.appendChild(body);

    // Optional tiny chart if a few numbers exist
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

    // Inline feedback controls (mapped to event where possible)
    if (eligible.has(t)) {
      const addControlsFor = (el) => {
        const key = norm(el.textContent || "");
        if (!key) return;

        const ev  = evByBody[key];      // may be undefined
        const arr = fbByBody.get(key) || [];
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

        // Save handler
        box.querySelector(".feedback-save").addEventListener("click", async () => {
          const txtEl = box.querySelector(".feedback-text");
          const note = (txtEl.value || "").trim();
          if (!note) return;
          try {
            await jsonFetch(api("feedback"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                event_id: ev?.id,
                analysis_id: row.id,
                category: canonicalizeTitle(t),
                body: key,
                note,
                kind: "context"
              })
            });
            // optimistic update
            const now = new Date().toISOString();
            const updated = [{ ts: now, note, kind: "context", body: key }, ...(fbByBody.get(key) || [])];
            fbByBody.set(key, updated);
            txtEl.value = "";
            const toggleBtn = box.querySelector(".feedback-toggle");
            if (toggleBtn) toggleBtn.textContent = `View (${updated.length})`;
            const listEl = box.querySelector(".feedback-list");
            if (listEl && !listEl.classList.contains("hidden")) renderFbList(listEl, updated);
          } catch (e) { console.error("Save feedback failed:", e); }
        });

        // View toggle
        const listEl = box.querySelector(".feedback-list");
        box.querySelector(".feedback-toggle").addEventListener("click", () => {
          listEl.classList.toggle("hidden");
          if (!listEl.classList.contains("hidden")) {
            renderFbList(listEl, fbByBody.get(key) || []);
          }
        });

        el.appendChild(box);
      };

      card.querySelectorAll("li, p").forEach(addControlsFor);
    }

    wrap.appendChild(card);
    layoutModalScroll();
  }
}



// ===========================
// Feedback Manager (UI)
// ===========================

// Tiny escape helper (uses your global escapeHtml if present)
function fbmEsc(s = "") {
  return (window.escapeHtml
    ? escapeHtml(s)
    : String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
}

async function loadFeedbacks({ q = "", entity_id = "", category = "" } = {}) {
  const params = new URLSearchParams();
  if (q)         params.set("q", q);
  if (entity_id) params.set("entity_id", entity_id);
  if (category)  params.set("category", category);
  params.set("limit", "500");
  return (await jsonFetch(api(`feedbacks?${params.toString()}`))) || [];
}

function renderEventsPager({ total, page, pages, startIndex }) {
  const el = $("eventsPager");
  if (!el) return;
  if (pages <= 1) { el.innerHTML = ""; return; }

  const end = Math.min(total, startIndex + EV_PAGE_SIZE);
  el.innerHTML = `
    <div class="flex items-center justify-center gap-2 py-3">
      <button class="pager-btn" data-page="first" ${page === 1 ? "disabled" : ""} title="First">&laquo;</button>
      <button class="pager-btn" data-page="prev"  ${page === 1 ? "disabled" : ""} title="Previous">&lsaquo;</button>
      <span class="pager-meta">Showing ${startIndex + 1}&ndash;${end} of ${total} • Page ${page}/${pages}</span>
      <button class="pager-btn" data-page="next"  ${page === pages ? "disabled" : ""} title="Next">&rsaquo;</button>
      <button class="pager-btn" data-page="last"  ${page === pages ? "disabled" : ""} title="Last">&raquo;</button>
    </div>
  `;

  // one delegated handler
  el.onclick = (ev) => {
    const btn = ev.target.closest(".pager-btn");
    if (!btn) return;
    const pages = Math.max(1, Math.ceil(EV_CACHE.length / EV_PAGE_SIZE));
    if (btn.dataset.page === "first") renderEventsPage(1);
    else if (btn.dataset.page === "prev") renderEventsPage(EV_PAGE - 1);
    else if (btn.dataset.page === "next") renderEventsPage(EV_PAGE + 1);
    else if (btn.dataset.page === "last") renderEventsPage(pages);
  };
}

function paintEventsList(list) {
  const box = $("eventsList");
  if (!box) return;
  box.innerHTML = "";

  list.forEach(ev => {
    const row = document.createElement("div");
    row.className = "event-row";
    const ts = ev.ts ? new Date(ev.ts).toLocaleString() : "";

    row.innerHTML = `
      <div class="event-meta">
        <div class="event-time">${escapeHtml(ts)}</div>
        <span class="${eventPillClass(ev.category)}">${escapeHtml(ev.category || "generic")}</span>
      </div>
      <div class="event-main">
        <div class="title">${escapeHtml(ev.title || "Event")}</div>
        <div class="body">${escapeHtml(ev.body || "")}</div>
      </div>
      <div class="event-actions">
        <button class="chip js-add">Add feedback</button>
        <button class="chip js-view">View feedback (${ev.feedback_count || 0})</button>
      </div>
      <div id="fb-list-${ev.id}" class="hidden mt-1 col-span-3"></div>
    `;

    row.querySelector(".js-add")?.addEventListener("click", () => {
      openFeedbackDialog({
        analysis_id: ev.analysis_id,
        event_id: ev.id,
        category: (ev.category || "generic"),
        body: ev.body || ""
      });
    });

    row.querySelector(".js-view")?.addEventListener("click", () => {
      toggleFeedbackList(ev.id);
    });
    queueMicrotask(() => resolveDeviceChips(listEl));
    box.appendChild(row);
  });
}

function renderEventsPage(page = 1) {
  const total = EV_CACHE.length;
  const pages = Math.max(1, Math.ceil(total / EV_PAGE_SIZE));
  EV_PAGE = Math.min(Math.max(1, page), pages);
  const start = (EV_PAGE - 1) * EV_PAGE_SIZE;
  const slice = EV_CACHE.slice(start, start + EV_PAGE_SIZE);
  paintEventsList(slice);
  renderEventsPager({ total, page: EV_PAGE, pages, startIndex: start });
}

function renderFeedbackList(rows) {
  const box = document.getElementById("fbm-list");
  if (!box) return;

  if (!rows.length) {
    box.innerHTML = "<div class='text-gray-400 text-sm'>No feedback found.</div>";
    return;
  }

  box.innerHTML = rows.map(r => {
    const ents = (r.entities || (r.entity_ids ? r.entity_ids.split(",") : []))
      .filter(Boolean)
      .map(e => `<span class="chip">${fbmEsc(e)}</span>`)
      .join(" ");

    const when = r.ts ? new Date(r.ts).toLocaleString() : "";

    return `
      <div class="p-3 rounded border border-white/10 hover:bg-white/5" data-id="${r.id}">
        <div class="flex items-start gap-3">
          <div class="text-xs text-gray-400 min-w-[20ch]">${fbmEsc(when)}</div>
          <span class="chip">${fbmEsc(r.category || "generic")}</span>
          <div class="flex-1">
            <div class="text-sm text-gray-300">${fbmEsc(r.title || r.body || "")}</div>
            <div class="text-sm mt-1 fbm-note">${fbmEsc(r.note || "")}</div>
            <div class="mt-1 flex flex-wrap gap-1">${ents}</div>
            <div class="mt-2 flex gap-2">
              <button class="chip fbm-edit"   data-edit="${r.id}">Edit</button>
              <button class="chip fbm-delete" data-del="${r.id}">Delete</button>
              <button class="chip"            data-open="${r.analysis_id}">Open analysis</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join("");

  // Edit -> open edit dialog
  box.querySelectorAll(".fbm-edit").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = Number(btn.getAttribute("data-edit"));
      const row = await jsonFetch(api(`feedback/${id}`));
      if (row) openFeedbackEditor(row);
    });
  });

  // Delete (inline)
  box.querySelectorAll(".fbm-delete").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = Number(btn.getAttribute("data-del"));
      if (!confirm("Delete this feedback permanently?")) return;
      try {
        await jsonFetch(api(`feedback/${id}`), { method: "DELETE" });
        btn.closest("[data-id]")?.remove();
      } catch (e) {
        console.error(e);
        alert("Failed to delete.");
      }
    });
  });

  // Open related analysis in your existing modal
  box.querySelectorAll("[data-open]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const aid = Number(btn.getAttribute("data-open"));
      const item = await jsonFetch(api(`history/${aid}`));
      if (item) openModal(item);
    });
  });
}

async function refreshFeedbackManager() {
  const q   = document.getElementById("fbm-search")?.value.trim() || "";
  const ent = document.getElementById("fbm-entity")?.value.trim() || "";
  const cat = document.getElementById("fbm-category")?.value || "";
  const rows = await loadFeedbacks({ q, entity_id: ent, category: cat });
  renderFeedbackList(rows);
}

function openFeedbackManager() {
  const dlg = document.getElementById("dlg-manage-feedback");
  if (!dlg) return;
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.classList.remove("hidden");
  refreshFeedbackManager().catch(console.error);
}

// ----- Edit dialog -----
function openFeedbackEditor(row) {
  const dlg = document.getElementById("dlg-edit-feedback");
  if (!dlg) return;

  const idEl   = document.getElementById("fbe-id");
  const noteEl = document.getElementById("fbe-note");
  const kindEl = document.getElementById("fbe-kind");
  const metaEl = document.getElementById("fbe-meta");
  const resEl  = document.getElementById("fbe-result");

  if (idEl)   idEl.value = row.id;
  if (noteEl) noteEl.value = row.note || "";
  if (kindEl) kindEl.value = (row.kind || "context");

  const ents = (row.entities || (row.entity_ids ? row.entity_ids.split(",") : []))
    .filter(Boolean)
    .map(e => `<span class="chip">${fbmEsc(e)}</span>`)
    .join(" ");

  const when = row.ts ? new Date(row.ts).toLocaleString() : "";

  if (metaEl) {
    metaEl.innerHTML = `${fbmEsc(when)} • <b>${fbmEsc(row.category || "generic")}</b><br>${
      fbmEsc(row.title || row.body || "")
    }<div class="mt-1">${ents}</div>`;
  }
  if (resEl) resEl.textContent = "";

  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.classList.remove("hidden");
}

async function saveFeedbackEdit() {
  const id   = Number(document.getElementById("fbe-id").value);
  const note = (document.getElementById("fbe-note").value || "").trim();
  const kind = (document.getElementById("fbe-kind").value || "context").trim();
  const resEl = document.getElementById("fbe-result");

  try {
    await jsonFetch(api(`feedback/${id}`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note, kind })
    });
    if (resEl) resEl.textContent = "Saved.";
    await refreshFeedbackManager();
    setTimeout(() => document.getElementById("dlg-edit-feedback").close?.(), 400);
  } catch (e) {
    console.error(e);
    if (resEl) resEl.textContent = "Failed to save.";
  }
}

async function deleteFeedbackEdit() {
  const id = Number(document.getElementById("fbe-id").value);
  const resEl = document.getElementById("fbe-result");
  if (!confirm("Delete this feedback permanently?")) return;

  try {
    await jsonFetch(api(`feedback/${id}`), { method: "DELETE" });
    if (resEl) resEl.textContent = "Deleted.";
    await refreshFeedbackManager();
    setTimeout(() => document.getElementById("dlg-edit-feedback").close?.(), 400);
  } catch (e) {
    console.error(e);
    if (resEl) resEl.textContent = "Failed to delete.";
  }
}

// ----- One-time wiring -----
function initFeedbackManager() {
  const btn = document.getElementById("btn-manage-feedback");
  const dlg = document.getElementById("dlg-manage-feedback");
  const ed  = document.getElementById("dlg-edit-feedback");

  if (btn) {
    btn.addEventListener("click", () => {
      console.log("Opening Feedback Manager…");
      openFeedbackManager();
    });
  }

  // Filters / refresh
  document.getElementById("fbm-refresh")?.addEventListener("click", refreshFeedbackManager);
  document.getElementById("fbm-search")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); refreshFeedbackManager(); }
  });
  document.getElementById("fbm-entity")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); refreshFeedbackManager(); }
  });
  document.getElementById("fbm-category")?.addEventListener("change", refreshFeedbackManager);

  // Close manager dialog
  document.getElementById("fbm-close")?.addEventListener("click", () => {
    dlg?.close?.(); dlg?.classList?.add("hidden");
  });

  // Edit dialog buttons
  document.getElementById("fbe-save")?.addEventListener("click", saveFeedbackEdit);
  document.getElementById("fbe-delete")?.addEventListener("click", deleteFeedbackEdit);
  document.getElementById("fbe-cancel")?.addEventListener("click", () => {
    ed?.close?.(); ed?.classList?.add("hidden");
  });

  // ESC to close dialogs
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (ed && !ed.open) return;
    ed?.close?.(); ed?.classList?.add("hidden");
    dlg?.close?.(); dlg?.classList?.add("hidden");
  });
}




// function escClose(e) { if (e.key === "Escape") closeModal(); }
function closeModal() {
  const ov = $("detailsOverlay");
  if (!ov) return;
  ov.classList.add("hidden");
  ov.style.display = "none"; // belt-and-suspenders against inline display
  if (ov._escHandler) {
    document.removeEventListener("keydown", ov._escHandler);
    ov._escHandler = null;
  }
}

// ---------- Init ----------
// ---------- Init ----------
function init() {
  // wire up buttons
  $("toggleMode").addEventListener("click", toggleMode);
  $("runAnalysis").addEventListener("click", runAnalysisNow);
  initFeedbackManager();

  // 🔧 Fallback click for unresolved device chips
  if (!window._deviceChipHandlerBound) {
    document.addEventListener("click", (e) => {
      const chip = e.target.closest(".device-chip-loader");
      if (!chip) return;
      const eid = chip.getAttribute("data-entity-id");
      const url = HA.url(`/config/devices/dashboard?search=${encodeURIComponent(eid)}`);
      window.open(url, "_blank", "noopener");
    }, { passive: true });
    window._deviceChipHandlerBound = true;
  }

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
