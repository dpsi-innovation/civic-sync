/* =====================================================
   CIVICSYNC — CIVIL APP LOGIC
   Tabs: DASHBOARD · LIVE · CIVIC · SYSTEM
   Uses the same one node as the admin console.
   ===================================================== */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
async function jget(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

/* =====================================================
   THEME + CLASSIC toggles (same tokens as console)
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
  const m = document.querySelector('meta[name="theme-color"]');
  if (m) m.content = t === "dark" ? "#0d141b" : "#f3f5f6";
  const b = $("themeToggle");
  if (b) b.textContent = t === "dark" ? "☀" : "☾";
}
function applyClassic(on) {
  if (on) document.documentElement.setAttribute("data-classic", "1");
  else document.documentElement.removeAttribute("data-classic");
  localStorage.setItem("cw-classic", on ? "1" : "0");
  const b = $("colorToggle");
  if (b) { b.textContent = on ? "◍" : "◐"; b.setAttribute("aria-pressed", String(on)); }
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
  if ($("themeToggle")) $("themeToggle").addEventListener("click", () =>
    applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
  if ($("colorToggle")) $("colorToggle").addEventListener("click", () =>
    applyClassic(document.documentElement.getAttribute("data-classic") !== "1"));
}

/* =====================================================
   TOAST
   ===================================================== */
function toast(msg, kind) {
  const zone = $("toast-zone");
  const el = document.createElement("div");
  el.className = "toast" + (kind === "critical" ? " critical" : "");
  el.textContent = msg;
  zone.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .4s"; }, 3400);
  setTimeout(() => el.remove(), 3900);
}

/* =====================================================
   TABS
   ===================================================== */
function setTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tabbtn").forEach(t => t.classList.remove("active"));
  const sec = $("tab-" + name);
  const btn = document.querySelector(`.tabbtn[data-tab="${name}"]`);
  if (sec) sec.classList.add("active");
  if (btn) btn.classList.add("active");
  if (name === "dashboard" && latestRows.length) renderCityMap(latestRows);
  window.scrollTo({ top: 0 });
}
function bindTabs() {
  document.querySelectorAll(".tabbtn").forEach(b =>
    b.addEventListener("click", () => setTab(b.dataset.tab)));
  document.querySelectorAll("[data-goto]").forEach(b =>
    b.addEventListener("click", () => setTab(b.dataset.goto)));
}

/* =====================================================
   STATUS PILL + LIVE POLLING (shared with dashboard tab)
   ===================================================== */
let lastStatus = null;
async function pollStatus() {
  try {
    const s = await jget("/api/status");
    lastStatus = s;
    const pill = $("appPill");
    const online = !!s.camera_online;
    pill.textContent = online ? "● live" : "◌ node syncing";
    pill.style.color = online ? "var(--ok)" : "var(--warn)";
    $("liveLabel").textContent = "SOURCE — " + (s.camera_label || (online ? "NODE CAM" : "OFFLINE"));
    if (s.camera_coords) { lastCoords = s.camera_coords; setupGMFrame(s.camera_coords); }
    $("liveCam").src = "/video?t=" + Date.now();
    $("camOff").hidden = online;
    $("lFps").textContent = s.fps ?? "–";
    $("lFaces").textContent = s.faces ?? 0;
    $("lLight").textContent =
      (typeof s.brightness === "number") ? Math.round(s.brightness) : "–";
    /* dashboard */
    $("dNode").textContent = online ? "ONLINE" : "SYNCING";
    $("dNodeFoot").textContent = s.mode === "LEARNING"
      ? "busy: AI retraining session" : "watching " + (s.camera_label || "the street").toLowerCase();
    $("dAnomaly").textContent =
      (typeof s.anomaly_score === "number") ? s.anomaly_score.toFixed(2) : "–";
  } catch {
    $("appPill").textContent = "○ node offline";
    $("camOff").hidden = false;
  }
}

/* dashboard report stats */
function renderDashReports(rows) {
  const open = rows.filter(r => r.status !== "Resolved").length;
  const done = rows.filter(r => r.status === "Resolved").length;
  $("dOpen").textContent = open;
  $("dResolved").textContent = done;
}
function renderRecent(rows) {
  const box = $("dashRecent");
  box.innerHTML = rows.slice(0, 5).map(r => `
    <div class="mini-item">
      <span class="sev-badge ${esc(r.severity)}">${esc(r.severity)}</span>
      <span><b>${esc(r.type)}</b><span class="msub">${esc(r.location)}</span></span>
      <span class="spacer">
        ${r.demo ? '<span class="sev-badge DEMO">DEMO</span>' : ""}
        <span class="${r.status === "Resolved" ? "resolved-dot" : r.status === "In Progress" ? "progress-dot" : "pending-dot"}"
              title="${esc(r.status)}"></span>
      </span>
    </div>`).join("") || '<div class="mini-item muted">city report log empty</div>';
}

/* my reports (per-device tracking) */
function myReports() { return JSON.parse(localStorage.getItem("cw-mine") || "[]"); }
function trackMine(row) {
  const m = myReports();
  m.unshift(row); m.splice(8);
  localStorage.setItem("cw-mine", JSON.stringify(m));
  renderMine();
}
function renderMine() {
  const box = $("myReports");
  const m = myReports();
  box.innerHTML = m.map(r => `
    <div class="mini-item">
      <span class="pending-dot" title="Pending"></span>
      <span><b>${esc(r.type)}</b><span class="msub">${esc(r.location)} · ${esc(r.status || "Pending")}</span></span>
      <span class="spacer"><span class="tag green">${esc(r.time)}</span></span>
    </div>`).join("") || '<div class="mini-item muted">nothing sent yet — your submissions appear here</div>';
}

let latestRows = [];
async function loadReports() {
  try {
    const rows = await jget("/api/reports");
    latestRows = rows;
    renderDashReports(rows);
    renderRecent(rows);
    renderCityMap(rows);
  } catch {}
}

/* =====================================================
   CITY MAP (Leaflet pins) + GOOGLE MAPS FEED
   ===================================================== */
let cityMap = null, cityPins = null, lastCoords = null, gmDidSetup = false;
function pinColor(r) {
  if (r.status === "Resolved") return "#27c58b";
  if (r.severity === "High") return "#e34d59";
  if (r.severity === "Medium") return "#d88a16";
  return "#27c58b";
}
function pinCoords(r, center) {
  if (r.coords && r.coords.lat != null) return { lat: r.coords.lat, lon: r.coords.lon };
  let h = 2166136261;
  for (const ch of String(r.id)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
  const lat = ((h >>> 0) % 1000) / 1000 - 0.5;
  const lon = (((h >>> 10) >>> 0) % 1000) / 1000 - 0.5;
  return { lat: center.lat + lat * 0.035, lon: center.lon + lon * 0.05 };
}
function renderCityMap(reports) {
  if (!window.L || $("cityMap").offsetHeight === 0) return;   /* tab hidden */
  const center = lastCoords || { lat: 28.6469, lon: 77.3613 };
  if (!cityMap) {
    cityMap = L.map("cityMap", { scrollWheelZoom: false }).setView([center.lat, center.lon], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(cityMap);
    cityPins = L.layerGroup().addTo(cityMap);
    cityMap._centered = true;
  }
  cityPins.clearLayers();
  reports.forEach(r => {
    const c = pinCoords(r, center);
    L.marker([c.lat, c.lon], {
      title: r.type,
      icon: L.divIcon({
        className: "",
        html: `<div class="cw-pin" style="width:28px;height:28px;border-radius:50%;
          background:${pinColor(r)};border:3px solid #fff;box-shadow:0 2px 8px #0006">${esc(r.type[0] || "?")}</div>`,
        iconSize: [28, 28], iconAnchor: [14, 14],
      }),
    }).bindPopup(
      `<b>${esc(r.type)}</b><br><span style="color:#687">${esc(r.location)}</span><br>
       <span class="sev-badge ${esc(r.severity)}">${esc(r.severity)}</span>
       ${r.demo ? '<span class="sev-badge DEMO">DEMO</span>' : "civil report"}`)
      .addTo(cityPins);
  });
}
function setupGMFrame(c) {
  if (gmDidSetup || !c) return;
  gmDidSetup = true;
  const frame = $("gmFrame");
  if (frame) frame.src =
    "https://maps.google.com/maps?q=" + c.lat + "," + c.lon + "&z=15&output=embed";
  const road = $("gmRoadLink");
  if (road) road.href = "https://www.google.com/maps/@" + c.lat + "," + c.lon + ",15z";
}

/* =====================================================
   SYSTEM TAB
   ===================================================== */
async function loadSystem() {
  try {
    const sys = await jget("/api/system");
    $("sAiName").textContent = sys.ai_version + " (CIVICSYNC " + sys.version + ")";
    $("sVersion").textContent = "v" + sys.version + " · " + sys.sector;
    $("sCam").textContent = sys.camera_label || sys.camera_id || "node cam";
  } catch {}
  try {
    const s = await jget("/api/status");
    const ai = s.ai_model || {};
    $("sSamples").textContent = ai.samples ?? "–";
    $("sThreshold").textContent = ai.threshold ?? "–";
    $("sMode").textContent = s.mode || "ONLINE";
    $("sFps").textContent = s.fps ?? "–";
  } catch {}
}

/* =====================================================
   CIVIC SUBMISSION — proof-verified capture
   ===================================================== */
const stream = { v: null };
async function openCam() {
  try {
    const st = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1440 } }, audio: false });
    stream.v = st;
    const v = $("cv");
    v.srcObject = st; v.hidden = false;
    $("shutter").disabled = false;
    $("camHint").textContent = "";
  } catch {
    $("camHint").innerHTML = "camera blocked — use <b>FILE (live capture mode)</b> below";
    try { $("photoInput").click(); } catch {}
  }
}
function closeCam() {
  try { stream.v && stream.v.getTracks().forEach(t => t.stop()); } catch {}
  stream.v = null;
}
let lastProof = null;

function bindCivic() {
  $("openCam").addEventListener("click", openCam);
  $("retake") && $("retake").addEventListener("click", () => {
    lastProof = null; setProofPill();
    closeCam(); openCam();
  });

  $("shutter").addEventListener("click", async () => {
    const v = $("cv");
    if (!v.videoWidth) { toast("Camera not ready yet…", "critical"); return; }
    try {
      lastProof = await CWProof.prepareFromStream(v, $("cs"));
      afterCapture();
    } catch (e) { toast("✖ " + e.message, "critical"); }
  });

  /* fallback: live-capture-mode file input (mobile camera roll blocked) */
  $("photoInput").addEventListener("change", async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    try {
      /* freshness guard: a pick older than 30 min is rejected */
      const age = (Date.now() - f.lastModified) / 60000;
      if (age > 30) {
        toast("✖ That image is " + Math.round(age) + " min old — take a live capture",
          "critical");
        $("proofPill").textContent = "✖ STALE PIC REJECTED — TAKE A FRESH CAPTURE";
        $("proofPill").className = "proof-pill bad";
        return;
      }
      lastProof = await CWProof.prepare("civilApp", f);
      afterCapture();
    } catch (err) { toast("✖ " + err.message, "critical"); }
  });

  $("rpSubmit").addEventListener("click", submitReport);
}

function afterCapture() {
  const v = $("cv"), p = $("photoPreview");
  p.src = "/snapshot?t=" + Math.random();   /* placeholder replaced below */
  try { p.src = URL.createObjectURL(new Blob(
    [Uint8Array.from(atob(lastProof.base64), c => c.charCodeAt(0))],
    { type: "image/jpeg" })); } catch {}
  p.hidden = false; v.hidden = true;
  closeCam();
  setProofPill();
}
function setProofPill() {
  const pill = $("proofPill");
  if (lastProof) {
    pill.textContent = "● PROOF EMBEDDED — EXIF " +
      lastProof.takenAt + (lastProof.device.lat != null
        ? " · GPS " + lastProof.device.lat.toFixed(4) + ", " + lastProof.device.lon.toFixed(4)
        : "");
    pill.className = "proof-pill ok";
  } else {
    pill.textContent = "◌ NO PROOF CAPTURED YET";
    pill.className = "proof-pill";
  }
}

async function submitReport(ev) {
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    if (!lastProof) throw new Error("Take the picture first — the node only accepts live proof");
    const type = $("rpType").value;
    const body = JSON.stringify({
      type, severity: $("rpSeverity").value,
      location: $("rpLocation").value.trim() || "unspecified",
      description: $("rpDescription").value.trim(),
      proof: lastProof
    });
    const res = await jget("/api/reports", {
      method: "POST", headers: { "Content-Type": "application/json" }, body
    });
    toast(res.verified
      ? "✔ REPORT SENT — proof verified by the node"
      : "Report sent — proof needs operator review",
      res.verified ? "ok" : null);
    trackMine({ type, location: $("rpLocation").value || "unspecified",
                status: "Pending", time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) });
    lastProof = null; setProofPill();
    $("photoPreview").src = "";
    $("photoPreview").hidden = true;
    $("rpLocation").value = ""; $("rpDescription").value = "";
    loadReports();
  } catch (err) {
    toast("✖ " + err.message, "critical");
  } finally { btn.disabled = false; }
}

/* =====================================================
   PWA INSTALL
   ===================================================== */
let deferredPrompt = null;
function bindInstall() {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    $("installBanner").hidden = false;
  });
  $("installBtn").addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    $("installBanner").hidden = true;
  });
  $("installX").addEventListener("click", () => { $("installBanner").hidden = true; });
  if ("serviceWorker" in navigator)
    navigator.serviceWorker.register("/sw.js").catch(() => {});
}

/* =====================================================
   BOOT
   ===================================================== */
window.addEventListener("DOMContentLoaded", () => {
  initToggles();
  bindTabs();
  bindCivic();
  bindInstall();
  renderMine();
  pollStatus();
  loadReports();
  loadSystem();
  setInterval(pollStatus, 2000);
  setInterval(loadReports, 8000);
  setInterval(loadSystem, 12000);
});
