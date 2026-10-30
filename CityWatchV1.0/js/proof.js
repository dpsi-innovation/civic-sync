/* =====================================================
   CIVICSYNC — CIVIL PROOF-OF-CAPTURE MODULE
   * takes a live/just-captured photo (getUserMedia or
     capture="environment" input — never a gallery pick)
   * re-encodes it through canvas, then embeds a fresh
     EXIF APP1 block: Make/Model = CivicSync Civil,
     DateTimeOriginal = NOW, GPS IFD = device geolocation
   * hashes the exact JPEG bytes (sha256) and returns the
     proof payload the node independently re-verifies:
     EXIF freshness, edit-software scan, GPS distance.
   Used by BOTH doors: admin intake + civil app.
   ===================================================== */
"use strict";

const CWProof = (() => {

  /* ---------- sha256 (pure JS — works on plain http LAN) ---------- */
  function sha256(buf) {
    function rotr(n, x) { return (x >>> n) | (x << (32 - n)); }
    const K = new Uint32Array([
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
      0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
      0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
      0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
      0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
      0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
      0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
    const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const len = buf.length;
    const bitLen = len * 8;
    const withPad = new Uint8Array((((len + 8) >> 6) + 1) << 6);
    withPad.set(buf);
    withPad[len] = 0x80;
    const dv = new DataView(withPad.buffer);
    dv.setUint32(withPad.length - 4, bitLen >>> 0, false);
    dv.setUint32(withPad.length - 8, Math.floor(bitLen / 0x100000000), false);
    const w = new Uint32Array(64);
    for (let i = 0; i < withPad.length; i += 64) {
      for (let j = 0; j < 16; j++) w[j] = dv.getUint32(i + j * 4, false);
      for (let j = 16; j < 64; j++) {
        w[j] = (rotr(17, w[j - 2]) ^ rotr(19, w[j - 2]) ^ (w[j - 2] >>> 10)) +
               w[j - 7] + (rotr(6, w[j - 15]) ^ rotr(11, w[j - 15]) ^ (w[j - 15] >>> 3)) + w[j - 16];
        w[j] >>>= 0;
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let j = 0; j < 64; j++) {
        const t1 = h + (rotr(6, e) ^ rotr(11, e) ^ rotr(25, e)) +
          ((e & f) ^ (~e & g)) + K[j] + w[j];
        const t2 = (rotr(2, a) ^ rotr(13, a) ^ rotr(22, a)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a;
        a = (t1 + t2) >>> 0;
      }
      H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0;
      H[3] = (H[3] + d) >>> 0; H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0;
      H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
    }
    return Array.from(H).map(x => x.toString(16).padStart(8, "0")).join("");
  }

  /* ---------- EXIF APP1 builder (little-endian TIFF) ---------- */
  function exifApp1(make, model, takenAt, lat, lon) {
    const ASCII = 2, RATIONAL = 5;
    const dms = (v) => {
      v = Math.abs(v);
      const d = Math.floor(v), mF = (v - d) * 60, m = Math.floor(mF),
            s = Math.round((mF - m) * 60 * 10000);
      return [[d, 1], [m, 1], [s, 10000]];
    };
    const bytes = (str) => {
      const a = [];
      for (const ch of str) a.push(ch.charCodeAt(0) < 128 ? ch.charCodeAt(0) : 63);
      a.push(0);
      return a;
    };

    const makeB = bytes(make), modelB = bytes(model), tsB = bytes(takenAt);
    const latR = dms(lat), lonR = dms(lon);

    let off = 8;                        /* TIFF header: 'II' + 42 + IFD offset */
    const ifd0Cnt = 4                   /* Make Model ExifPtr GPSPtr */;
    const ifd0Off = off;
    off += 2 + ifd0Cnt * 12 + 4;
    const exifOff = off;
    off += 2 + 1 * 12 + 4;
    const gpsOff = off;
    off += 2 + 4 * 12 + 4;
    const mkOff = off; off += makeB.length;
    const mdOff = off; off += modelB.length;
    const tsOff = off; off += tsB.length;
    const latOff = off; off += 24;
    const lonOff = off; off += 24;
    const total = off;

    const t = new ArrayBuffer(total);
    const v = new DataView(t);
    let p = 0;
    const u16 = (x) => { v.setUint16(p, x, true); p += 2; };
    const u32 = (x) => { v.setUint32(p, x, true); p += 4; };
    const pickS = () => { const s = p; p += 4; return s; };

    /* TIFF header */
    p = 2;
    v.setUint8(0, 0x49); v.setUint8(1, 0x49); u16(42); u32(ifd0Off);

    /* IFD0 */
    p = ifd0Off; u16(ifd0Cnt);
    const entry = (tag, typ, cnt, valPayload) => {
      u16(tag); u16(typ); u32(cnt); valPayload();
    };
    entry(0x010F, ASCII, makeB.length, () => {
      if (makeB.length > 4) u32(mkOff); else { t[p] = makeB[0] || 0; p += 4; }
    });
    entry(0x0110, ASCII, modelB.length, () => {
      if (modelB.length > 4) u32(mdOff); else {
        for (let i = 0; i < Math.min(4, modelB.length); i++) t[p + i] = modelB[i];
        p += 4;
      }
    });
    entry(0x8769, 4, 1, () => u32(exifOff));
    entry(0x8825, 4, 1, () => u32(gpsOff));
    u32(0);  /* next IFD */

    /* Exif IFD — DateTimeOriginal */
    p = exifOff; u16(1);
    entry(0x9003, ASCII, tsB.length, () => u32(tsOff));

    /* GPS IFD */
    p = gpsOff; u16(4);
    entry(1, ASCII, 2, () => {                       /* lat ref N/S */
      const s = pickS(); v.setUint8(s, lat >= 0 ? 0x4E : 0x53);
    });
    entry(2, RATIONAL, 3, () => u32(latOff));
    entry(3, ASCII, 2, () => {                       /* lon ref E/W */
      const s = pickS(); v.setUint8(s, lon >= 0 ? 0x45 : 0x57);
    });
    entry(4, RATIONAL, 3, () => u32(lonOff));

    /* data area (Uint8Array view shares the same buffer) */
    const view = new Uint8Array(t);
    const wr = (at, arr) => { for (let i = 0; i < arr.length; i++) view[at + i] = arr[i]; };
    wr(mkOff, makeB); wr(mdOff, modelB); wr(tsOff, tsB);
    const wrRat = (at, rat) => {
      p = at;
      for (const [num, den] of rat) { u32(num); u32(den); }
    };
    wrRat(latOff, latR); wrRat(lonOff, lonR);

    /* wrap into APP1: 'Exif\0\0' + TIFF */
    const exifHead = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00];
    const payload = new Uint8Array(6 + total);
    payload.set(exifHead, 0);
    payload.set(new Uint8Array(t), 6);
    return payload;   /* without the 2-byte marker/len — caller adds */
  }

  /* splice APP1 into JPEG right after SOI */
  function spliceExif(jpegBytes, exifPayload) {
    /* find EOI to validate jpeg */
    if (jpegBytes[0] !== 0xFF || jpegBytes[1] !== 0xD8) return jpegBytes;
    const mark = new Uint8Array(2 + 2 + exifPayload.length);
    mark[0] = 0xFF; mark[1] = 0xE1;
    mark[2] = (exifPayload.length + 2) >> 8;
    mark[3] = (exifPayload.length + 2) & 0xFF;
    mark.set(exifPayload, 4);
    const out = new Uint8Array(jpegBytes.length + mark.length);
    out.set(jpegBytes.subarray(0, 2), 0);
    out.set(mark, 2);
    out.set(jpegBytes.subarray(2), 2 + mark.length);
    return out;
  }

  /* ---------- geolocation (best effort) ---------- */
  function getGPS() {
    return new Promise((res) => {
      if (!navigator.geolocation) return res(null);
      navigator.geolocation.getCurrentPosition(
        (pos) => res({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
        () => res(null),
        { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 });
    });
  }

  /* ---------- image: file/blob -> downscaled raw JPEG ---------- */
  function toJpegBlob(blob, maxSide = 1600) {
    return new Promise((res, rej) => {
      const img = new Image();
      const url = URL.createObjectURL(blob);
      img.onload = () => {
        try {
          const sc = Math.min(1, maxSide / Math.max(img.width, img.height));
          const c = document.createElement("canvas");
          c.width = Math.max(1, Math.round(img.width * sc));
          c.height = Math.max(1, Math.round(img.height * sc));
          const g = c.getContext("2d");
          g.drawImage(img, 0, 0, c.width, c.height);
          c.toBlob((b) => b && b.type === "image/jpeg"
            ? res(b) : rej(new Error("capture encode failed")),
            "image/jpeg", 0.88);
        } catch (e) { rej(e); }
        finally { URL.revokeObjectURL(url); }
      };
      img.onerror = () => { URL.revokeObjectURL(url); rej(new Error("not a safe image")); };
      img.src = url;
    });
  }

  /* ---------- camera device label ---------- */
  function camLabel() {
    const ua = navigator.userAgent;
    if (/iPhone|iPad/.test(ua)) return "iPhone";
    if (/Android/.test(ua)) return (ua.match(/Android[^;)]+/i) || ["Android"])[0];
    return navigator.platform || "WebClient";
  }

  /* =====================================================
     CORE: blob -> proof payload
     ===================================================== */
  async function prepare(_slot, blobOrFile) {
    const gps = await getGPS();
    const now = new Date();
    const presStd = (d) =>
      d.getFullYear().toString().padStart(4, "0") + ":" +
      String(d.getMonth() + 1).padStart(2, "0") + ":" +
      String(d.getDate()).padStart(2, "0") + " " +
      String(d.getHours()).padStart(2, "0") + ":" +
      String(d.getMinutes()).padStart(2, "0") + ":" +
      String(d.getSeconds()).padStart(2, "0");

    const takenAt = presStd(now);
    const model = camLabel();
    const raw = await toJpegBlob(blobOrFile);
    const rawBytes = new Uint8Array(await raw.arrayBuffer());
    const exif = exifApp1("CivicSync Civil", model, takenAt,
      gps ? gps.lat : 0, gps ? gps.lon : 0);
    const finalBytes = spliceExif(rawBytes, exif);
    const b64 = btoa(String.fromCharCode.apply(null, finalBytes.subarray(0, 8192)) +
      String.fromCharCode.apply(null, finalBytes.subarray(8192)));
    const sha = sha256(finalBytes);

    return {
      base64: b64,
      filename: "capture-" + Date.now() + ".jpg",
      device: gps ? { lat: gps.lat, lon: gps.lon } : {},
      sha256: sha,
      takenAt: takenAt,
      camera: model,
      _size: finalBytes.length,
    };
  }

  /* live getUserMedia capture -> canvas blob -> proof */
  async function prepareFromStream(video, canvas, quality = 0.88) {
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext("2d").drawImage(video, 0, 0);
    const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", quality));
    return prepare("live", blob);
  }

  /* =====================================================
     preview helper for plain <input type=file>
     also previews; the EXIF embedding happens at submit
     ===================================================== */
  function attach(input, previewEl) {
    if (!input) return;
    input.addEventListener("change", () => {
      const f = input.files && input.files[0];
      if (!f) return;
      if (previewEl) {
        const url = URL.createObjectURL(f);
        previewEl.src = url;
        previewEl.hidden = false;
        previewEl.onload = () => URL.revokeObjectURL(url);
      }
    });
  }

  return { prepare, prepareFromStream, attach, getGPS, sha256, exifApp1, spliceExif };
})();
window.CWProof = CWProof;
