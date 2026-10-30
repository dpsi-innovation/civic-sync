# 🔱 CivicSync v1.0 — Intelligent City Surveillance Grid

> Industrial-grade city monitoring: **live camera → trained AI → industrial dashboard → mobile PWA app.**
> Built on the RoadGuard AI citizen-reporting prototype (report engine preserved), upgraded into a
> full operations system.

---

## What's inside

| Layer | Tech | Detail |
|---|---|---|
| **Vision AI perception** | OpenCV 5 + numpy | MOG2 adaptive background subtraction (motion zones), Haar person/face detection, per-frame feature encoding (P1 faces, M1 motion, L1 light, E1 edges) |
| **Trained AI (ours)** | numpy Gaussian model | `models/scene_baseline.npz` — a multivariate-Gaussian **scene anomaly model** trained on live capture (`ai/train_model.py`, shipped trained, `--demo` seeded or `--live` from your webcam). Scores each frame with Mahalanobis distance; flags deviation from the *learned normal* scene |
| **Node server** | Flask + threads | MJPEG annotated feed `/video`, SSE live event stream `/api/events`, alert journal `data/alerts.jsonl`, hot **in-dashboard retraining** (`POST /api/retrain`) |
| **Web dashboard** | HTML/CSS/JS | Two full themes — **DARK (control-room)** and **LIGHT (daylight ops)** — hazard-cam grid, event log, AI model card, hazard-report manager (RoadGuard base preserved) |
| **Mobile app** | PWA | Installable app at `/app` (phone layout, bottom tabs, push notifications, offline shell via service worker) |

## Quick start — ONE node, two doors

```bat
run.bat
```
or
```bash
python server.py
```

**There is one CivicSync node.** The Admin console and the Civil app are two
views of the same node — not separate programs:

| Door | URL | Who |
|---|---|---|
| **ADMIN CONSOLE** | `http://localhost:8000` | operators — live grid, AI card, alert journal, report review (approve / resolve), export |
| **CIVIL APP** | `http://localhost:8000/app` | citizens + phones — installable PWA, live feed, civic reports with photo proof |

The `Admin\` and `Civil\` folders are just shortcut doors that open those two
URLs. Everything (AI, camera, reports) is served by the single node.

## 🎞 Demo dataset — the app runs in front of you

`demoset/street_people.mp4` is bundled and wired in as the **first camera source**
("DEMO DATASET – CITY STREET (REPLAY)"). It replays continuously through the
trained AI — people counted, motion tracked, anomaly scores plotted — so the
grid always has something to show, with no camera or internet required. Real
sources (Caltrans CCTV or your webcam) follow it in `config.json`.

A seeded **demo civic corpus** (`data/civic_reports.jsonl`, `cw-demo-*` rows) is
also loaded on first boot so the reports table and civil feed are already
alive at presentation time.

## ✔ Using it on other devices (phones, tablets, other PCs)

Everything runs on the PC that launched `server.py` — nothing to install anywhere else:

1. Keep the **PC terminal open** (it is the city node).
2. On startup it prints your **LAN links and a scan-able QR code**. The dashboard also shows live links in the **"Open on Other Devices"** panel (backed by `/api/network`).
3. **Phone / tablet (same Wi-Fi):** scan the QR or type `http://<PC-LAN-IP>:8000/app`. First visit shows an **INSTALL** banner — CivicSync lands on the home screen as a native-looking app.
4. **Other PCs / TVs:** open `http://<PC-LAN-IP>:8000` in any browser.
5. **Blocked from connecting?** Accept the Windows Firewall prompt once ("Allow access on private networks"). Headless machines:
   ```powershell
   # run once in an Administrator terminal
   netsh advfirewall firewall add rule name="CivicSync" dir=in action=allow protocol=TCP localport=8000
   ```

> Want access beyond the school Wi-Fi (internet-over)? Point a tunnel such as `cloudflared` or `ngrok` at port 8000 — the dashboard and PWA work unchanged through the tunnel URL.

## Retrain the AI your way

The trained model ships in the box, but you can respecialise it to *your* scene:

* **From the dashboard:** "⟳ RETRAIN ON LIVE FEED" — the node recaptures ~60 s of your real scene and refits the baseline live.
* **From the CLI:**
  ```bash
  python -m ai.train_model --live --seconds 45     # your webcam, ~45 s capture
  python -m ai.train_model --demo                  # deterministic synthetic session
  ```
Whenever the anomaly score crosses the learned threshold, a **CRITICAL** alert lands in the event log, journal and mobile app toast.

## Project layout

```
CivicSyncV1.0/
├─ server.py              Flask node: camera pipeline, MJPEG, SSE, retrain, journal
├─ config.json            camera source, thresholds, HUD & server settings
├─ ai/
│  ├─ detector.py         perception: motion (MOG2) + faces + feature encoder + HUD burn-in
│  ├─ anomaly.py          ▸ THE TRAINED MODEL: Gaussian scene baseline (load/score/save)
│  ├─ train_model.py      trainer: python -m ai.train_model --live | --demo
│  └─ models/scene_baseline.npz   shipped trained weight file
├─ web/
│  ├─ index.html          command dashboard (dark + light industrial themes)
│  ├─ app.html            mobile PWA app
│  ├─ css/style.css       merged industrial design system (RoadGuard layout + ops skin)
│  ├─ css/app.css         app skin
│  ├─ js/dashboard.js     reports engine + node client (status/SSE/charts/retrain)
│  ├─ js/app.js           app client: live, alerts, notifications, install, civic taps
│  ├─ manifest.webmanifest  PWA manifest
│  ├─ sw.js               service worker (installable, offline shell)
│  └─ icons/              generated app icons (192/512)
├─ data/alerts.jsonl      persistent alert journal (auto-created)
└─ run.bat                one-double-click launcher (Windows)
```

## The RoadGuard lineage

The original RoadGuard AI dashboard (sidebar, welcome card, hazard report form, search/filter/delete
table, recent activity, Teachable-Machine citizen scanner, dark/light toggle) was preserved **cell
for cell** under `/dashboard`, `/scanner`, `/report`, `/reports` sections — and now shares the
civicsync node: citizen scans and city AI alerts live in one industrial system.

## Notes for demo day

* If no camera is attached the node shows a **NO SIGNAL** industrial test card and auto-retries every 3 s — everything else keeps running.
* To change camera: edit `config.json → camera.source` (index `0`, or an RTSP/HTTP URL).
* Model card in the dashboard shows the trained baseline (sensor means, threshold, training session, timestamp) — the AI is verifiable, not magic.
* Teachable Machine scanner needs the TM TensorFlow.js snippet if you use it (README tools note); citizen scanner otherwise simply photographs and logs.
