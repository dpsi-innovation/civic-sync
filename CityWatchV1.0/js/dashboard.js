/* =====================================================
   CIVICSYNC — ADMIN CONSOLE DASHBOARD
   Binds every control in index.html to the ONE node API.
   ===================================================== */
"use strict";

const $ = (id) => document.getElementById(id);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ---------- shared polys (also used by app.js) ---------- */
window.$cw$ = $;

async function gwJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await gwErr(r));
  return r.json();
}
async function gwErr(r) {
  try { const j = await r.json(); return j.error || r.statusText; }
  catch { return r.statusText; }
}
function gwTime(ts) {
  const d = ts ? new Date(ts) : new Date();
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
window.gwJSON = gwJSON; window.gwErr = gwErr;
window.gwTime = gwTime; window.esc = esc;

/* =====================================================
   TOASTS
   ===================================================== */
function toast(msg, kind) {
  const zone = $("toast-zone");
  if (!zone) return;
  const el = document.createElement("div");
  el.className = "toast" + (kind === "critical" ? " critical" : "");
  el.textContent = msg;
  zone.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .4s"; }, 3600);
  setTimeout(() => el.remove(), 4100);
}

/* =====================================================
   THEME  (DARK OPS / LIGHT OPS)
   ===================================================== */
function themeFromLS() {
  const t = localStorage.getItem("cw-theme");
  if (t === "light" || t === "dark") return t;
  return (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches)
    ? "light" : "dark";
}
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  localStorage.setItem("cw-theme", t);
  const btn = $("themeToggle");
  if (btn) {
    btn.textContent = t === "dark" ? "☀ LIGHT OPS" : "☾ DARK OPS";
    btn.setAttribute("aria-pressed", t === "dark" ? "false" : "true");
  }
}
function applyClassic(on) {
  if (on) document.documentElement.setAttribute("data-classic", "1");
  else document.documentElement.removeAttribute("data-classic");
  localStorage.setItem("cw-classic", on ? "1" : "0");
  const btn = $("colorToggle");
  if (btn) {
    btn.textContent = on ? "◍ MODERN" : "◐ CLASSIC";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

/* ===== ACCENT PICKER — user picks ANY colour for the theme ===== */
const ACCENT_DEFAULT = "#f5a623";
function applyAccent(hex, save) {
  document.documentElement.style.setProperty("--lime", hex);   // every accent CSS var routes through --lime
  if (save !== false) localStorage.setItem("cw-accent", hex);
  const btn = $("accentBtn");
  if (btn) btn.style.setProperty("--swatch", hex);
}
function initAccent() {
  const saved = localStorage.getItem("cw-accent");
  if (saved) applyAccent(saved, false);
  const pick = $("accentPick"), btn = $("accentBtn");
  if (!pick || !btn) return;
  pick.value = saved || ACCENT_DEFAULT;
  applyAccent(pick.value, false);
  btn.addEventListener("click", () => pick.click());
  pick.addEventListener("input", () => applyAccent(pick.value));   // live preview while dragging
  pick.addEventListener("change", () => applyAccent(pick.value));  // final + persist
}
function initToggles() {
  applyTheme(themeFromLS());
  applyClassic(localStorage.getItem("cw-classic") === "1");
  initAccent();
  $("themeToggle").addEventListener("click", () =>
    applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
  $("colorToggle").addEventListener("click", () =>
    applyClassic(document.documentElement.getAttribute("data-classic") !== "1"));
}

/* =====================================================
   CANVAS SPARKLINES  (#chartFaces / #chartScores)
   ===================================================== */
function drawSpark(canvas, series, color, maxV) {
  if (!canvas) return;
  const w = canvas.clientWidth || 400, h = canvas.clientHeight || 54;
  canvas.width = w * devicePixelRatio; canvas.height = h * devicePixelRatio;
  const g = canvas.getContext("2d");
  g.scale(devicePixelRatio, devicePixelRatio);
  const css = getComputedStyle(document.documentElement);
  const line = css.getPropertyValue("--line").trim() || "#31414c";
  const txt = css.getPropertyValue("--faint").trim() || "#63737f";
  g.clearRect(0, 0, w, h);
  /* baseline grid */
  g.strokeStyle = line; g.globalAlpha = .5; g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, h - 1); g.lineTo(w, h - 1); g.stroke();
  g.globalAlpha = 1;
  if (!series || !series.length) {
    g.fillStyle = txt; g.font = "10px Consolas,monospace";
    g.fillText("awaiting node telemetry…", 6, h / 2 + 3);
    return;
  }
  const max = maxV || Math.max(1, ...series);
  const step = series.length > 1 ? w / (series.length - 1) : w;
  g.beginPath();
  series.forEach((v, i) => {
    const x = i * step, y = h - 3 - (v / max) * (h - 8);
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  });
  g.strokeStyle = color; g.lineWidth = 1.6; g.lineJoin = "round"; g.stroke();
  /* soft fill */
  g.lineTo(w, h); g.lineTo(0, h); g.closePath();
  g.fillStyle = color; g.globalAlpha = .13; g.fill(); g.globalAlpha = 1;
}

/* =====================================================
   LIVE STATUS POLLING  (/api/status)
   ===================================================== */
function renderStatus(s) {
  $("ledSystem").classList.toggle("ok", true);
  $("lblMode").textContent = s.mode || "ONLINE";
  $("ledAI").classList.toggle("ok", !!s.ai_model?.ready);

  $("statFaces").textContent = s.faces ?? 0;
  $("statAnomaly").textContent =
    (typeof s.anomaly_score === "number") ? s.anomaly_score.toFixed(2) : "–";
  $("kFps").textContent = s.fps ?? "–";
  $("kMotion").textContent = s.motion_boxes ?? 0;
  $("kLight").textContent =
    (typeof s.brightness === "number") ? Math.round(s.brightness) : "–";
  $("kEdge").textContent =
    (typeof s.edge_density === "number") ? s.edge_density.toFixed(2) : "–";

  const online = !!s.camera_online;
  $("liveCam").src = "/video?t=" + Date.now();   // keep the MJPEG rolling
  if (s.camera_coords) { lastCoords = s.camera_coords; setupGMFrame(lastCoords); }
  $("camOff").hidden = online;
  $("camSource").textContent =
    "SOURCE — " + (s.camera_label || (online ? "NODE CAM" : "OFFLINE"));

  drawSpark($("chartFaces"), s.faces_series, "#3ca6a7", null);
  drawSpark($("chartScores"), s.scores, "#c6f36b", null);

  const ai = s.ai_model || {};
  $("aiBadge").textContent =
    ai.ready ? "● TRAINED · " + (ai.mode || "trusty") : "◌ LEARNING";
  $("aiBadge").className = "tag " + (ai.ready ? "green" : "amber");
  $("aiSamples").textContent = ai.samples ?? "–";
  $("aiThreshold").textContent = ai.threshold ?? "–";
  $("aiTrainedAt").textContent = ai.trained_at ?? "–";
  $("retrainHint").textContent = $("retrainBtn").disabled
    ? "Retrain session running… collecting live vectors." :
    "Retraining re-fits the baseline on the last minutes of scene data.";
}

let statusTimer = null;
function pollStatus() {
  clearInterval(statusTimer);
  const run = gwJSON("/api/status").then(renderStatus).catch(() => {
    $("camOff").hidden = false;
    $("lblMode").textContent = "NODE DOWN";
  });
  statusTimer = setInterval(() => {
    gwJSON("/api/status").then(renderStatus).catch(() => {
      $("camOff").hidden = false;
      $("lblMode").textContent = "NODE DOWN";
    });
  }, 1600);
  return run;
}

/* =====================================================
   SYSTEM IDENTITY  (/api/system)
   ===================================================== */
function renderSystem(sys) {
  $("aiVersion").textContent = sys.ai_version + "  ·  CIVICSYNC " + sys.version;
  $("aiMode").textContent = sys.model_ready ? sys.sector + " · node-segmented" : "booting…";
}

/* =====================================================
   EVENT JOURNAL  (SSE /api/events)
   ===================================================== */
let sse = null;
function onEvent(ev) {
  if (ev.severity === "CRITICAL") toast("⚠ " + ev.title, "critical");

  /* keep the overview stats in sync with the node */
  loadReports().catch(() => {});
  gwJSON("/api/status").then(renderStatus).catch(() => {});
}

function connectSSE() {
  try { sse && sse.close(); } catch {}
  sse = new EventSource("/api/events");
  sse.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch {} };
  sse.onerror = () => {};
}

/* =====================================================
   CITY REPORTS  (/api/reports)
   ===================================================== */
let reports = [];
const hidden = new Set(JSON.parse(localStorage.getItem("cw-hidden") || "[]"));

function reportSource(r) {
  if (r.demo) return "DEMO DATASET";
  if (r.evidence?.device) return "CIVIL APP · " + r.evidence.device;
  return "CIVIL APP";
}
function sevBadge(s) { return `<span class="sev-badge ${esc(s)}">${esc(s)}</span>`; }

function renderReports() {
  const q = ($("searchReports").value || "").toLowerCase();
  const fs = $("filterSeverity").value, fst = $("filterStatus").value;
  const rows = reports.filter(r => !hidden.has(r.id))
    .filter(r => !fs || r.severity === fs)
    .filter(r => !fst || r.status === fst)
    .filter(r => !q ||
      (r.type + " " + r.location + " " + r.description).toLowerCase().includes(q));

  const tb = $("reportsBody");
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="6" class="empty">No reports match this view.</td></tr>';
  } else {
    tb.innerHTML = rows.map(r => `
      <tr>
        <td><b>${esc(r.type)}</b><br><span style="color:var(--dim);font-size:11.5px">${esc(r.description || "")}</span></td>
        <td>${esc(r.location)}<br><span style="color:var(--faint);font-size:10px;font-family:var(--mono)">${esc(r.date || "")}</span></td>
        <td>${sevBadge(r.severity)}</td>
        <td>
          <select data-rid="${esc(r.id)}" class="rstat" style="background:var(--panel-2);border:1px solid var(--line);color:var(--text);border-radius:8px;padding:4px 6px;font-size:12px">
            ${["Pending", "In Progress", "Resolved"].map(s =>
              `<option ${s === r.status ? "selected" : ""}>${s}</option>`).join("")}
          </select>
        </td>
        <td style="font-size:11px;color:var(--dim)">
          ${r.demo ? sevBadge("DEMO") : ""}
          ${r.verified ? '<span style="color:var(--ok)">✔ verified</span>' : '<span style="color:var(--warn)">◌ unverified</span>'}
          <br>${esc(reportSource(r))}${r.evidence?.gps_mismatch_m != null ?
            ` · GPS Δ${r.evidence.gps_mismatch_m}m` : ""}
        </td>
        <td><button data-del="${esc(r.id)}" class="btn ghost small">✕</button></td>
      </tr>`).join("");
  }

  /* KPI stats */
  $("statReports").textContent = reports.filter(r => !hidden.has(r.id)).length;
  $("statCritical").textContent =
    reports.filter(r => !hidden.has(r.id) && r.severity === "High" && r.status !== "Resolved").length;
}

async function loadReports() {
  reports = await gwJSON("/api/reports");
  renderReports();
  renderCityMap(reports);
}

function bindReportControls() {
  ["searchReports", "filterSeverity", "filterStatus"].forEach(id =>
    $(id).addEventListener("input", renderReports));
  $("reportsBody").addEventListener("change", async (e) => {
    const sel = e.target.closest(".rstat");
    if (!sel) return;
    try {
      await gwJSON(`/api/reports/${encodeURIComponent(sel.dataset.rid)}/status`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: sel.value }) });
      const r = reports.find(x => String(x.id) === sel.dataset.rid);
      if (r) r.status = sel.value;
      toast("Report " + sel.dataset.rid + " → " + sel.value);
    } catch (err) { toast("✖ " + err.message, "critical"); }
  });
  $("reportsBody").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-del]");
    if (!btn) return;
    hidden.add(btn.dataset.del);
    localStorage.setItem("cw-hidden", JSON.stringify([...hidden]));
    renderReports();
    renderCityMap(reports);
    toast("Report hidden from console view");
  });
  $("exportBtn").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(reports, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "civicsync-report-log-" + new Date().toISOString().slice(0, 10) + ".json";
    a.click();
    toast("Report log exported");
  });
}

/* =====================================================
   AI RETRAIN
   ===================================================== */
function bindRetrain() {
  $("retrainBtn").addEventListener("click", async () => {
    try {
      const res = await gwJSON("/api/retrain", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ samples: 240 }) });
      if (!res.ok) { toast("Retrain already running — hold tight"); return; }
      $("retrainBtn").disabled = true;
      $("retrainHint").textContent =
        "Collecting 240 live scene vectors… the table updates when the new baseline lands.";
      toast("⟳ Retrain session started — watching the scene");
      setTimeout(() => { $("retrainBtn").disabled = false; }, 60000);
    } catch (err) { toast("✖ " + err.message, "critical"); }
  });
}

/* =====================================================
   LIVE CITY MAP  (Leaflet pins + Google Maps road feed)
   ===================================================== */
let cityMap = null, cityPins = null, lastCoords = null, gmDidSetup = false;

function pinColor(r) {
  if (r.status === "Resolved") return "#27c58b";
  if (r.severity === "High") return "#e34d59";
  if (r.severity === "Medium") return "#d88a16";
  return "#27c58b";
}

/* demo rows carry no GPS: deterministic jitter around the node camera */
function pinCoords(r, center) {
  if (r.coords && r.coords.lat != null) return { lat: r.coords.lat, lon: r.coords.lon };
  let h = 2166136261;
  for (const ch of String(r.id)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
  const lat = ((h >>> 0) % 1000) / 1000 - 0.5;         /* ±.5 */
  const lon = (((h >>> 10) >>> 0) % 1000) / 1000 - 0.5;
  return { lat: center.lat + lat * 0.035, lon: center.lon + lon * 0.05 };
}

function renderCityMap(reports) {
  if (!window.L) return;
  const center = lastCoords || { lat: 28.6469, lon: 77.3613 };
  if (!cityMap) {
    cityMap = L.map("cityMap", { scrollWheelZoom: true }).setView([center.lat, center.lon], 13);
    const tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap contributors", maxZoom: 19 }).addTo(cityMap);
    tiles.on("load", () => {
      const t = $("mapTag");
      if (t) { t.textContent = "● TILES LIVE"; t.className = "tag green"; }
    });
    cityPins = L.layerGroup().addTo(cityMap);
  }
  if (!cityMap._centered) {
    cityMap.setView([center.lat, center.lon], 13);
    cityMap._centered = true;
  }
  cityPins.clearLayers();
  reports.filter(r => !hidden.has(r.id)).forEach(r => {
    const c = pinCoords(r, center);
    const color = pinColor(r);
    const marker = L.marker([c.lat, c.lon], {
      title: r.type,
      icon: L.divIcon({
        className: "",
        html: `<div class="cw-pin" style="width:30px;height:30px;border-radius:50%;
               background:${color};border:3px solid #fff;box-shadow:0 2px 8px #0006;
               ${r.status === "Resolved" ? "opacity:.45;" : ""}">${esc(r.type[0] || "?")}</div>`,
        iconSize: [30, 30], iconAnchor: [15, 15],
      }),
    });
    marker.bindPopup(
      `<b style="font-size:14px">${esc(r.type)}</b><br>
       <span style="color:#687">${esc(r.location)}</span><br>
       <span class="sev-badge ${esc(r.severity)}">${esc(r.severity)}</span>
       ${r.status !== "Pending" ? `<span class="sev-badge ${esc(r.status)}">${esc(r.status)}</span>` : ""}
       <br><small>${r.demo ? "DEMO dataset row" : "civil submission"}</small>`);
    marker.addTo(cityPins);
  });
  if (reports.length && !cityMap._fitDone) {
    const b = cityPins.getBounds();
    if (b.isValid()) { cityMap.fitBounds(b.pad(.25)); }
    cityMap._fitDone = true;
  }
}

/* Google Maps road feed: key-free embed centered on the node camera */
function setupGMFrame(c) {
  if (gmDidSetup || !c) return;
  gmDidSetup = true;
  const frame = $("gmFrame");
  if (frame) frame.src =
    `https://maps.google.com/maps?q=${c.lat},${c.lon}&z=15&output=embed`;
  const road = $("gmRoadLink"), traffic = $("gmTrafficLink");
  const coords = `${c.lat},${c.lon}`;
  if (road) road.href = `https://www.google.com/maps/@${coords},15z`;
  if (traffic) traffic.href = `https://www.google.com/maps/@${coords},15z/data=!5m1!1e1`;
}

/* nav smooth-highlight */
function bindNav() {
  $$(".nav-link").forEach(a => a.addEventListener("click", function () {
    $$(".nav-link").forEach(x => x.classList.remove("active"));
    this.classList.add("active");
    window.matchMedia && matchMedia("(max-width:760px)").matches && this.scrollIntoView();
  }));
}

/* =====================================================
   REPORT INTAKE / SCANNER  (#hgSubmit)
   ===================================================== */
function bindScanner() {
  const input = $("photoInput"), prev = $("photoPreview");
  input.addEventListener("change", () => {
    const f = input.files && input.files[0];
    if (!f) { prev.hidden = true; return; }
    CWProof.attach(input, prev);   /* preview handled inside proof module */
  });
  $("hgSubmit").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const file = input.files && input.files[0];
      if (!file) throw new Error("Attach a live capture photo first");
      const type = $("hgType").value.trim();
      if (!type) throw new Error("Name the hazard");
      const proof = await CWProof.prepare("adminConsole", file);
      const body = JSON.stringify({
        type, severity: $("hgSeverity").value,
        location: $("hgLocation").value.trim() || "unspecified",
        description: $("hgDescription").value.trim(),
        proof
      });
      const res = await gwJSON("/api/reports", {
        method: "POST", headers: { "Content-Type": "application/json" }, body
      });
      toast(res.verified
        ? "✔ Report filed & PROOF VERIFIED" + (res.gps_mismatch_m != null ? " (GPS Δ" + res.gps_mismatch_m + "m)" : "")
        : "Report filed — proof unverified", res.verified ? "ok" : "critical");
      ["hgType", "hgLocation", "hgDescription"].forEach(id => $(id).value = "");
      input.value = ""; prev.hidden = true;
      loadReports();
    } catch (err) {
      toast("✖ " + err.message, "critical");
    } finally { btn.disabled = false; }
  });
}

/* =====================================================
   BOOT
   ===================================================== */
window.addEventListener("DOMContentLoaded", () => {
  initToggles();
  bindNav();
  bindReportControls();
  bindRetrain();
  bindScanner();
  pollStatus();
  gwJSON("/api/system").then(renderSystem).catch(() => {});
  loadReports().catch(() => toast("⚠ could not load city reports", "critical"));
  connectSSE();
});
