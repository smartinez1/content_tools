// content_tools Studio - Frontend Application with Lo-Fi Emo-Trap Engine

let currentSong = null;
let timings = [];
let presets = {};
let activeJobPollInterval = null;

// Style state
let styleState = {
  font_name: "Helvetica",
  font_size: 48,
  box_width: 680,
  lowercase: true,
  primary_color: "#FFFF00",
  secondary_color: "#FFFFFF",
  outline_color: "#000000",
  back_color: "#80000000",
  text_opacity: 0.88,
  bold: true,
  italic: false,
  outline: 4.0,
  shadow: 2.0,
  alignment: 2,
  margin_l: 70,
  margin_r: 70,
  margin_v: 760,
  pos_x: null,
  pos_y: null,
  fade_in_ms: 150,
  fade_out_ms: 250,
  opacity_trailing: true,
  karaoke: true,
  karaoke_tag: "kf",
  blur: 0.0,
  pop_scale: 0.0,
  letter_spacing: 0,
  time_offset: 0,
};

// Lo-Fi Emo-Trap Aesthetic State. Defaults and knob ranges come from GET /api/aesthetics
// (single source of truth in video_fx.py); this minimal object is only the pre-load fallback.
let aestheticState = {
  enabled: true,
  color_wash: true,
  color_grade: false,
  framerate_decimate: true,
  iso_noise: true,
  chromatic_aberration: true,
  fogged_glass_vignette: true,
  crt_scanlines: true,
  sidechain_pump: true,
  fuzzed_glitch: true,
  ca_shift_px: 3,
};
let fxSchema = null;

// DOM elements
const songSelect = document.getElementById("songSelect");
const songStatusBadge = document.getElementById("songStatusBadge");
const btnRealign = document.getElementById("btnRealign");
const mainVideo = document.getElementById("mainVideo");
const subOverlay = document.getElementById("subOverlay");
const subText = document.getElementById("subText");
const videoContainer = document.getElementById("videoContainer");
const stageFx = document.getElementById("stageFx");

const timeDisplay = document.getElementById("timeDisplay");
const timelineSlider = document.getElementById("timelineSlider");
const linesList = document.getElementById("linesList");
const lineCount = document.getElementById("lineCount");

// Typography & Layout elements
const presetSelect = document.getElementById("presetSelect");
const btnSavePreset = document.getElementById("btnSavePreset");
const btnRenamePreset = document.getElementById("btnRenamePreset");
const btnDeletePreset = document.getElementById("btnDeletePreset");
const fontInput = document.getElementById("fontInput");
const fontsList = document.getElementById("fontsList");
const fontSize = document.getElementById("fontSize");
const fontSizeVal = document.getElementById("fontSizeVal");
const boxWidth = document.getElementById("boxWidth");
const boxWidthVal = document.getElementById("boxWidthVal");
const fontBold = document.getElementById("fontBold");
const fontItalic = document.getElementById("fontItalic");
const fontLowercase = document.getElementById("fontLowercase");
const letterSpacing = document.getElementById("letterSpacing");

// Colors & Appearance
const primaryColor = document.getElementById("primaryColor");
const secondaryColor = document.getElementById("secondaryColor");
const outlineColor = document.getElementById("outlineColor");
const textOpacity = document.getElementById("textOpacity");
const textOpacityVal = document.getElementById("textOpacityVal");
const outlineWidth = document.getElementById("outlineWidth");
const outlineVal = document.getElementById("outlineVal");
const shadowDepth = document.getElementById("shadowDepth");
const shadowVal = document.getElementById("shadowVal");
const blurAmount = document.getElementById("blurAmount");
const blurVal = document.getElementById("blurVal");

// Position elements
const marginV = document.getElementById("marginV");
const marginVVal = document.getElementById("marginVVal");
const posX = document.getElementById("posX");
const posXVal = document.getElementById("posXVal");
const posY = document.getElementById("posY");
const posYVal = document.getElementById("posYVal");

// Motion elements
const fadeIn = document.getElementById("fadeIn");
const fadeInVal = document.getElementById("fadeInVal");
const timeOffset = document.getElementById("timeOffset");
const timeOffsetVal = document.getElementById("timeOffsetVal");
const offsetMinus = document.getElementById("offsetMinus");
const offsetPlus = document.getElementById("offsetPlus");
const offsetReset = document.getElementById("offsetReset");
const fadeOut = document.getElementById("fadeOut");
const fadeOutVal = document.getElementById("fadeOutVal");
const opacityTrailing = document.getElementById("opacityTrailing");
const karaokeToggle = document.getElementById("karaokeToggle");
const karaokeTag = document.getElementById("karaokeTag");

// Aesthetic toggles
const lofiMasterToggle = document.getElementById("lofiMasterToggle");
const safeAreaToggle = document.getElementById("safeAreaToggle");
const safeTop = document.getElementById("safeTop");
const safeBottom = document.getElementById("safeBottom");
const safeSides = document.getElementById("safeSides");
const safeReadout = document.getElementById("safeReadout");
const reelsToggle = document.getElementById("reelsToggle");
const safeGuidesToggle = document.getElementById("safeGuidesToggle");
const reelsOverlay = document.getElementById("reelsOverlay");

// Output formats (/api/formats). Everything the UI computes in "script pixels" (caption position,
// safe area, line fitting) uses the format's reference canvas RW x RH: 1080x1920 for mobile,
// 1920x1080 for YouTube (libass scales it to the real file, even a 4K one).
const BUILTIN_FORMATS = [
  { id: "mobile", label: "Mobile 9:16", ref_w: 1080, ref_h: 1920, subdir: "",
    safe_area: { top: 130, bottom: 260, sides: 50 }, safe_limits: { top: 500, bottom: 500, sides: 250 },
    safe_default_on: true },
  { id: "youtube", label: "YouTube 16:9", ref_w: 1920, ref_h: 1080, subdir: "youtube",
    safe_area: { top: 54, bottom: 54, sides: 96 }, safe_limits: { top: 300, bottom: 300, sides: 500 },
    safe_default_on: false },
];
let formats = BUILTIN_FORMATS;
let FORMAT = formats[0];
let RW = FORMAT.ref_w;
let RH = FORMAT.ref_h;
const fmtQuery = () => `format=${encodeURIComponent(FORMAT.id)}`;

// Social safe area: margins (px of the reference frame) the whole picture is shrunk to fit inside.
// Mirrors lyrics_sync.parse_safe_area / safe_area_style so the preview matches the render.
let safeDefaults = { ...FORMAT.safe_area };
let safeState = { enabled: FORMAT.safe_default_on, ...safeDefaults };
let stageSafe = null; // the safe area the picture currently on the stage was rendered with

// One saved setting per format (the mobile one used to live under the plain "safeAreaCfg" key).
const safeKey = (id) => (id === "mobile" ? "safeAreaCfg" : `safeAreaCfg.${id}`);
function loadSafeState() {
  safeDefaults = { ...FORMAT.safe_area };
  safeState = { enabled: FORMAT.safe_default_on, ...safeDefaults };
  try {
    const saved = JSON.parse(localStorage.getItem(safeKey(FORMAT.id)) || "null");
    if (saved && typeof saved === "object") {
      for (const k of ["top", "bottom", "sides"]) {
        if (Number.isFinite(saved[k])) safeState[k] = saved[k];
      }
      if (typeof saved.enabled === "boolean") safeState.enabled = saved.enabled;
    }
  } catch (_) {}
}
loadSafeState();

function safePayload() {
  return safeState.enabled
    ? { top: safeState.top, bottom: safeState.bottom, sides: safeState.sides }
    : false;
}

// Where the shrunk picture sits, in reference-frame pixels (same maths as _safe_area_origin).
function safeGeometry(sa) {
  const boxW = (RW - 2 * sa.sides) / RW;
  const boxH = (RH - sa.top - sa.bottom) / RH;
  const s = Math.min(boxW, boxH);
  return { s, x0: sa.sides + (boxW * RW - s * RW) / 2, y0: sa.top + (boxH * RH - s * RH) / 2, boxW, boxH };
}

function syncSafeControls() {
  safeAreaToggle.checked = safeState.enabled;
  safeTop.value = safeState.top;
  safeBottom.value = safeState.bottom;
  safeSides.value = safeState.sides;
  document.getElementById("safeTopVal").textContent = safeState.top;
  document.getElementById("safeBottomVal").textContent = safeState.bottom;
  document.getElementById("safeSidesVal").textContent = safeState.sides;
  [safeTop, safeBottom, safeSides].forEach((el) => (el.disabled = !safeState.enabled));
  if (safeState.enabled) {
    const g = safeGeometry(safeState);
    safeReadout.textContent =
      `Picture shrinks to ${Math.round(g.s * 100)}% (${Math.round(g.s * RW)}\u00d7${Math.round(g.s * RH)} px).`;
  } else {
    safeReadout.textContent = "Off: the full frame is exported.";
  }
}

function saveSafeState() {
  try {
    localStorage.setItem(safeKey(FORMAT.id), JSON.stringify(safeState));
  } catch (_) {}
}
const fxControls = document.getElementById("fxControls");
const btnFxFrame = document.getElementById("btnFxFrame");
const btnFxClip = document.getElementById("btnFxClip");
const btnFxOriginal = document.getElementById("btnFxOriginal");
const btnFxResetAll = document.getElementById("btnFxResetAll");
const fxAutoRefresh = document.getElementById("fxAutoRefresh");
const fxFrameStatus = document.getElementById("fxFrameStatus");

// Action Buttons
const btnPreview10s = document.getElementById("btnPreview10s");
const btnRenderFull = document.getElementById("btnRenderFull");
const btnRenderClean = document.getElementById("btnRenderClean");
const jobCard = document.getElementById("jobCard");
const jobTitle = document.getElementById("jobTitle");
const jobProgressBar = document.getElementById("jobProgressBar");
const jobMessage = document.getElementById("jobMessage");
const jobOutputLinks = document.getElementById("jobOutputLinks");
const btnCancelJob = document.getElementById("btnCancelJob");
const previewPanel = document.getElementById("previewPanel");
const previewVideo = document.getElementById("previewVideo");
const btnClosePreview = document.getElementById("btnClosePreview");

let activeJobId = null;
const PREVIEW_SECONDS = 10;

// Buttons that start a long job; disabled while one is running.
const jobButtons = () => [btnRealign, btnPreview10s, btnRenderFull, btnRenderClean];
function setJobButtonsDisabled(disabled) {
  jobButtons().forEach((b) => b && (b.disabled = disabled));
}

// Where the 10s preview should start, always in *source* song time (mainVideo is never
// swapped for the preview, so its currentTime is the song position).
function pickPreviewStart() {
  const t = mainVideo.currentTime;
  if (Number.isFinite(t) && t > 0.05) return t;
  // Playhead untouched: begin at the first lyric line so the preview has captions.
  const first = timings.length ? timings[0].start + lyricOffset() : 0;
  return Math.max(0, first - 0.5);
}

function windowHasLyrics(start, duration) {
  const end = start + duration;
  const off = lyricOffset();
  return timings.some((l) => l.end + off > start && l.start + off < end);
}

// ---------------------------------------------------------------------------
// Global subtitle offset: shifts every caption (player + renders) without touching the line times.
// Lyric time = video time - offset.
// ---------------------------------------------------------------------------
const lyricOffset = () => Number(styleState.time_offset) || 0;
const offsetKey = (song) => `ct.offset:${song}`;

let offsetSaveTimer = null;

function setTimeOffset(value, { persist = true } = {}) {
  const v = Math.max(-5, Math.min(5, Math.round((Number(value) || 0) * 100) / 100));
  styleState.time_offset = v;
  timeOffset.value = v;
  timeOffsetVal.textContent = (v > 0 ? "+" : "") + v.toFixed(2);
  if (persist && currentSong) {
    // Saved on the server (.lyrics_sync/song_settings.json), debounced while the slider drags.
    const song = currentSong;
    const fmtId = FORMAT.id;
    clearTimeout(offsetSaveTimer);
    offsetSaveTimer = setTimeout(() => {
      fetch(`/api/song-settings/${encodeURIComponent(song)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ time_offset: v, format: fmtId }),
      }).catch(() => { /* offline: the value still applies for this session */ });
    }, 300);
  }
  updateOverlay();
}

async function restoreSongOffset(song) {
  setTimeOffset(0, { persist: false });
  let saved = 0;
  try {
    const res = await fetch(`/api/song-settings/${encodeURIComponent(song)}?${fmtQuery()}`);
    if (res.ok) saved = Number((await res.json()).time_offset) || 0;
  } catch (_) { /* ignore */ }
  // One-time move of offsets that older versions kept in this browser.
  if (!saved && FORMAT.id === "mobile") {
    try {
      const legacy = Number(localStorage.getItem(offsetKey(song))) || 0;
      if (legacy) {
        saved = legacy;
        localStorage.removeItem(offsetKey(song));
        if (song === currentSong) setTimeOffset(legacy); // persists it to the server
        return;
      }
    } catch (_) { /* ignore */ }
  }
  if (song === currentSong) setTimeOffset(saved, { persist: false });
  // First time this song is opened in a non-mobile format: measure the offset to the mobile video.
  if (!saved && FORMAT.id !== "mobile" && song === currentSong && !detectedBefore(song)) {
    detectOffset({ auto: true });
  }
}

const detectedKey = (song) => `ct.offsetDetected:${song}:${FORMAT.id}`;
function detectedBefore(song) {
  try { return localStorage.getItem(detectedKey(song)) === "1"; } catch (_) { return false; }
}

// Compare this format's soundtrack with the mobile video the lyrics were aligned on.
async function detectOffset({ auto = false } = {}) {
  const song = currentSong;
  const fmtId = FORMAT.id;
  const status = document.getElementById("offsetDetectStatus");
  const btn = document.getElementById("btnDetectOffset");
  if (!song || fmtId === "mobile") return;
  status.hidden = false;
  status.textContent = "Measuring the offset to the mobile video...";
  btn.disabled = true;
  try {
    const res = await fetch(`/api/song-settings/${encodeURIComponent(song)}/detect-offset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: fmtId }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    if (song !== currentSong || fmtId !== FORMAT.id) return; // the user moved on
    try { localStorage.setItem(detectedKey(song), "1"); } catch (_) { /* ignore */ }
    const sign = body.offset > 0 ? "+" : "";
    const conf = `confidence ${Math.round(body.confidence * 100)}%`;
    if (body.reliable) {
      setTimeOffset(body.offset);
      status.textContent = `Detected ${sign}${body.offset.toFixed(2)} s (${conf}) and applied.`;
    } else {
      status.textContent = `Could not match the sound reliably (${sign}${body.offset.toFixed(2)} s, ${conf}); offset left unchanged, adjust it by ear.`;
    }
  } catch (err) {
    if (auto) status.hidden = true; // e.g. there is no mobile video to compare with: stay quiet
    else status.textContent = `Detect failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

// Initialize app
document.addEventListener("DOMContentLoaded", async () => {
  await loadFonts();
  await initAesthetics();
  await loadPresets();
  await initFormats();
  await loadSongs();
  setupEventListeners();
  setupLineEditor();
  setupDragReposition();
  setupOverlayTracking();
  setupPanels();
  setupSafeArea();
});

// The caption overlay is positioned in pixels from the video's on-screen box, so it must be
// recomputed whenever that box changes - not only on playback ticks.
function setupOverlayTracking() {
  let queued = false;
  const refresh = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      updateOverlay();
    });
  };
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(refresh);
    ro.observe(mainVideo);
    ro.observe(videoContainer);
  }
  window.addEventListener("resize", refresh);
  ["loadedmetadata", "loadeddata", "seeked", "emptied"].forEach((ev) =>
    mainVideo.addEventListener(ev, refresh)
  );
}

function closePreviewModal() {
  previewVideo.pause();
  previewVideo.removeAttribute("src");
  previewVideo.load();
  previewPanel.hidden = true;
}

// Back to the plain source frame (also drops any in-flight FX render, whose result would be stale).
function hideStageFx() {
  fxFrameReq += 1;
  if (fxFrameAbort) fxFrameAbort.abort();
  clearTimeout(fxFrameTimer);
  stageFx.hidden = true;
  stageSafe = null;
  fxFrameStatus.textContent = "";
  updateOverlay();
}

// ---------------------------------------------------------------------------
// Social safe area controls + Instagram Reels overlay
function reelsSvg() {
  const w = "#fff";
  const icon = (x, y, scale, paths) =>
    `<g transform="translate(${x - 12 * scale} ${y - 12 * scale}) scale(${scale})" fill="none" stroke="${w}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${paths}</g>`;
  const count = (x, y, t) =>
    `<text x="${x}" y="${y}" fill="${w}" font-size="30" font-weight="600" text-anchor="middle">${t}</text>`;
  const nav = [108, 324, 540, 756, 972]
    .map((x, i) =>
      i === 2
        ? `<rect x="${x - 34}" y="1822" width="68" height="68" rx="16" fill="none" stroke="${w}" stroke-width="4"/><path d="M${x - 8} 1840l22 12-22 12z" fill="${w}"/>`
        : i === 4
          ? `<circle cx="${x}" cy="1856" r="30" fill="#777" stroke="${w}" stroke-width="3"/>`
          : `<circle cx="${x}" cy="1856" r="26" fill="none" stroke="${w}" stroke-width="4"/>`
    )
    .join("");
  return `
    <defs>
      <linearGradient id="rlTop" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity=".5"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>
      <linearGradient id="rlBot" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".7"/></linearGradient>
    </defs>
    <rect x="0" y="0" width="1080" height="300" fill="url(#rlTop)"/>
    <rect x="0" y="1380" width="1080" height="540" fill="url(#rlBot)"/>
    <g font-family="Helvetica, Arial, sans-serif">
      <text x="70" y="76" fill="${w}" font-size="38" font-weight="700">9:41</text>
      <rect x="930" y="48" width="68" height="32" rx="9" fill="none" stroke="${w}" stroke-width="3"/>
      <rect x="936" y="54" width="48" height="20" rx="5" fill="${w}"/>
      <text x="48" y="190" fill="${w}" font-size="62" font-weight="700">Reels</text>
      ${icon(1010, 160, 4.2, '<rect x="3" y="7" width="18" height="13" rx="3"/><circle cx="12" cy="13.5" r="3.4"/><path d="M8 7l1.4-2.5h5.2L16 7"/>')}
      ${icon(985, 1090, 4.6, '<path d="M12 21s-7-4.5-9.5-9C.8 8.3 3 4.5 6.7 4.5c2 0 3.6 1 5.3 3 1.7-2 3.3-3 5.3-3 3.7 0 5.9 3.8 4.2 7.5C19 16.5 12 21 12 21z"/>')}
      ${count(985, 1170, "12.4K")}
      ${icon(985, 1262, 4.6, '<path d="M21 11.5a8.5 8.5 0 0 1-12.4 7.5L3 20.5l1.6-5A8.5 8.5 0 1 1 21 11.5z"/>')}
      ${count(985, 1342, "318")}
      ${icon(985, 1432, 4.6, '<path d="M22 3L2 10.5l7.5 3L12.5 21 22 3zM9.500 13.5L22 3"/>')}
      ${count(985, 1512, "2.1K")}
      ${icon(985, 1600, 4.6, '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>')}
      <rect x="945" y="1655" width="80" height="80" rx="18" fill="#444" stroke="${w}" stroke-width="5"/>
      <circle cx="96" cy="1540" r="50" fill="#555" stroke="${w}" stroke-width="4"/>
      <text x="170" y="1552" fill="${w}" font-size="42" font-weight="700">oto</text>
      <rect x="290" y="1512" width="160" height="60" rx="16" fill="none" stroke="${w}" stroke-width="3"/>
      <text x="370" y="1554" fill="${w}" font-size="32" font-weight="600" text-anchor="middle">Follow</text>
      <text x="48" y="1642" fill="${w}" font-size="38">new snippet out 20.11 &#127925; stream it everywhere</text>
      <text x="48" y="1692" fill="${w}" font-size="38" opacity=".75">... more</text>
      <rect x="48" y="1722" width="420" height="58" rx="29" fill="#fff" fill-opacity=".22"/>
      <text x="76" y="1762" fill="${w}" font-size="32">&#9835; Original audio</text>
      <rect x="0" y="1790" width="1080" height="130" fill="#000" fill-opacity=".92"/>
      ${nav}
    </g>`;
}

// YouTube player (1920x1080 reference): title bar, control bar with the progress line, branding
// watermark, and optionally the end-screen element layout (last 5-20 s of the video).
function youtubeSvg() {
  const w = "#fff";
  const end = document.getElementById("ytEndToggle")?.checked;
  const ico = (x, y, paths, sw = 3, sc = 1) =>
    `<g transform="translate(${x} ${y}) scale(${sc})" fill="none" stroke="${w}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${paths}</g>`;
  const dashed = (x, y, wd, ht, label, rx = 8) =>
    `<rect x="${x}" y="${y}" width="${wd}" height="${ht}" rx="${rx}" fill="#fff" fill-opacity=".08" stroke="${w}" stroke-width="3" stroke-dasharray="16 10"/>` +
    `<text x="${x + wd / 2}" y="${y + ht / 2 + 8}" fill="${w}" font-size="26" text-anchor="middle" opacity=".85">${label}</text>`;
  return `
    <defs>
      <linearGradient id="ytTop" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity=".65"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>
      <linearGradient id="ytBot" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".75"/></linearGradient>
    </defs>
    <rect x="0" y="0" width="1920" height="170" fill="url(#ytTop)"/>
    <rect x="0" y="880" width="1920" height="200" fill="url(#ytBot)"/>
    <g font-family="Roboto, Helvetica, Arial, sans-serif">
      <text x="56" y="82" fill="${w}" font-size="40" font-weight="500">espacio tiempo (2am)</text>
      ${ico(1700, 50, '<circle cx="12" cy="12" r="10"/><path d="M12 7v5l3 2"/>', 3)}
      ${ico(1800, 54, '<circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/>', 4)}
      <rect x="48" y="1004" width="1824" height="6" rx="3" fill="#fff" fill-opacity=".3"/>
      <rect x="48" y="1004" width="640" height="6" rx="3" fill="#f00"/>
      <circle cx="688" cy="1007" r="13" fill="#f00"/>
      <path d="M62 1040l26 16-26 16z" fill="${w}"/>
      ${ico(122, 1038, '<path d="M3 9v6h4l5 4V5L7 9z"/><path d="M16 8a5 5 0 0 1 0 8"/>', 2.4, 1.4)}
      <text x="214" y="1068" fill="${w}" font-size="28">2:14 / 3:40</text>
      ${ico(1616, 1038, '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 11h4M14 11h4M6 15h8"/>', 2, 1.5)}
      ${ico(1690, 1038, '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>', 2, 1.5)}
      ${ico(1764, 1038, '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M12 12h6"/>', 2, 1.5)}
      ${ico(1830, 1038, '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>', 2, 1.5)}
      ${dashed(1748, 800, 124, 124, "logo", 62)}
      ${end ? dashed(96, 250, 640, 360, "video") + dashed(1184, 250, 640, 360, "video") + `<circle cx="960" cy="430" r="96" fill="#fff" fill-opacity=".08" stroke="${w}" stroke-width="3" stroke-dasharray="16 10"/><text x="960" y="438" fill="${w}" font-size="26" text-anchor="middle" opacity=".85">subscribe</text>` : ""}
    </g>`;
}

function renderReelsOverlay() {
  const showReels = reelsToggle.checked;
  const showBox = safeGuidesToggle.checked;
  reelsOverlay.hidden = !(showReels || showBox);
  if (reelsOverlay.hidden) return;
  let inner = showReels ? (FORMAT.id === "mobile" ? reelsSvg() : youtubeSvg()) : "";
  if (showBox) {
    const g = safeGeometry(safeState);
    inner += `<rect x="${safeState.sides}" y="${safeState.top}" width="${RW - 2 * safeState.sides}" height="${RH - safeState.top - safeState.bottom}" fill="none" stroke="#22d3ee" stroke-width="4" stroke-dasharray="22 14"/>`;
  }
  reelsOverlay.innerHTML = `<svg viewBox="0 0 ${RW} ${RH}" preserveAspectRatio="none">${inner}</svg>`;
  updateOverlay();
}

// ---------------------------------------------------------------------------
// Output formats (Mobile 9:16 / YouTube 16:9)
async function initFormats() {
  try {
    const r = await fetch("/api/formats");
    if (r.ok) {
      const d = await r.json();
      if (d.formats && d.formats.length) formats = d.formats;
    }
  } catch (_) { /* built-in definitions are used */ }
  let saved = "mobile";
  try { saved = localStorage.getItem("ct.format") || "mobile"; } catch (_) { /* ignore */ }
  renderFormatSwitch();
  setFormat(formats.some((f) => f.id === saved) ? saved : formats[0].id, { reload: false });
}

function renderFormatSwitch() {
  const box = document.getElementById("formatSwitch");
  if (!box) return;
  box.replaceChildren(
    ...formats.map((f) => {
      const b = document.createElement("button");
      b.className = "fmt-btn";
      b.dataset.format = f.id;
      b.textContent = f.label;
      b.addEventListener("click", () => f.id !== FORMAT.id && setFormat(f.id));
      return b;
    })
  );
}

// Each format keeps its own caption look (a mobile 48 px / 760 px-margin style makes no sense on the
// 1920x1080 canvas). The first time a format is used it starts from the current look plus the
// format's caption defaults.
const STYLE_STORE_KEY = "ct.styleByFormat";
function readStyleStore() {
  try { return JSON.parse(localStorage.getItem(STYLE_STORE_KEY) || "{}") || {}; } catch (_) { return {}; }
}
function swapCaptionStyle(prevId, next) {
  const store = readStyleStore();
  if (prevId) {
    const { time_offset: _o, ...look } = styleState;
    store[prevId] = look;
    try { localStorage.setItem(STYLE_STORE_KEY, JSON.stringify(store)); } catch (_) { /* ignore */ }
  }
  if (store[next.id]) applyStyle(store[next.id]);
  else if (next.caption_defaults && Object.keys(next.caption_defaults).length) {
    applyStyle(next.caption_defaults);
  }
}

function setFormat(id, { reload = true } = {}) {
  const prevId = FORMAT.id;
  FORMAT = formats.find((f) => f.id === id) || formats[0];
  swapCaptionStyle(reload ? prevId : "", FORMAT);
  RW = FORMAT.ref_w;
  RH = FORMAT.ref_h;
  try { localStorage.setItem("ct.format", FORMAT.id); } catch (_) { /* ignore */ }
  document.querySelectorAll(".fmt-btn").forEach((b) => b.classList.toggle("active", b.dataset.format === FORMAT.id));
  loadSafeState();
  safeTop.max = FORMAT.safe_limits.top;
  safeBottom.max = FORMAT.safe_limits.bottom;
  safeSides.max = FORMAT.safe_limits.sides;
  const mobile = FORMAT.id === "mobile";
  document.getElementById("overlayLabelText").textContent = mobile ? "Instagram Reels overlay" : "YouTube player overlay";
  document.getElementById("ytEndLabel").hidden = mobile;
  document.getElementById("offsetDetectRow").hidden = mobile;
  document.getElementById("offsetDetectStatus").hidden = true;
  document.dispatchEvent(new CustomEvent("ct:format", { detail: FORMAT.id }));
  syncSafeControls();
  renderReelsOverlay();
  if (reload) {
    hideStageFx();
    loadSongs();
  }
}

function setupSafeArea() {
  const onChange = () => {
    syncSafeControls();
    saveSafeState();
    renderReelsOverlay();
    if (!safeState.enabled && !fxAutoRefresh.checked) hideStageFx();
    else scheduleFxFrame();
  };
  safeAreaToggle.addEventListener("change", () => {
    safeState.enabled = safeAreaToggle.checked;
    onChange();
  });
  [[safeTop, "top"], [safeBottom, "bottom"], [safeSides, "sides"]].forEach(([el, key]) => {
    el.addEventListener("input", () => {
      safeState[key] = parseInt(el.value, 10);
      onChange();
    });
  });
  document.getElementById("btnSafeReset").addEventListener("click", () => {
    Object.assign(safeState, safeDefaults);
    onChange();
  });
  reelsToggle.addEventListener("change", renderReelsOverlay);
  document.getElementById("ytEndToggle").addEventListener("change", renderReelsOverlay);
  document.getElementById("btnDetectOffset").addEventListener("click", () => detectOffset());
  safeGuidesToggle.addEventListener("change", renderReelsOverlay);
  syncSafeControls();
  renderReelsOverlay();
}

function setupPanels() {
  btnClosePreview.addEventListener("click", closePreviewModal);
  // click on the dimmed backdrop (not the dialog) closes the preview
  previewPanel.addEventListener("click", (e) => {
    if (e.target === previewPanel) closePreviewModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!previewPanel.hidden) closePreviewModal();
  });
}

async function loadFonts() {
  try {
    const res = await fetch("/api/fonts");
    const data = await res.json();
    fontsList.innerHTML = "";
    (data.fonts || []).forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f;
      fontsList.appendChild(opt);
    });
  } catch (err) {
    console.warn("Failed to load fonts:", err);
  }
}

async function loadPresets(selectName) {
  try {
    const res = await fetch("/api/presets");
    presets = await res.json();
    const previous = selectName ?? presetSelect.value;
    presetSelect.innerHTML = "";
    const groups = [
      ["Built-in", Object.keys(presets).filter((n) => presets[n].builtin)],
      ["My presets", Object.keys(presets).filter((n) => !presets[n].builtin)],
    ];
    groups.forEach(([label, names]) => {
      if (!names.length) return;
      const group = document.createElement("optgroup");
      group.label = label;
      names.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        group.appendChild(opt);
      });
      presetSelect.appendChild(group);
    });
    if (previous && presets[previous]) presetSelect.value = previous;
    updatePresetButtons();
  } catch (err) {
    console.warn("Failed to load presets:", err);
  }
}

// Rename/Delete only make sense for presets the user saved, not the built-in ones.
function updatePresetButtons() {
  const p = presets[presetSelect.value];
  const editable = !!p && !p.builtin;
  btnRenamePreset.disabled = !editable;
  btnDeletePreset.disabled = !editable;
}

async function presetRequest(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || `Request failed (${res.status})`);
    return false;
  }
  return true;
}

async function loadSongs() {
  try {
    const res = await fetch(`/api/songs?${fmtQuery()}`);
    const data = await res.json();
    songSelect.innerHTML = '<option value="">-- Choose Song --</option>';
    data.songs.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.name + (s.video_path ? "" : " (No video)");
      opt.disabled = !s.video_path;
      songSelect.appendChild(opt);
    });

    // keep the current song when the new format has it, otherwise the first playable one
    const pick = data.songs.find((s) => s.name === currentSong && s.video_path) || data.songs.find((s) => s.video_path);
    if (pick) {
      songSelect.value = pick.name;
      selectSong(pick.name);
    } else {
      mainVideo.removeAttribute("src");
      mainVideo.load();
      hideStageFx();
    }
  } catch (err) {
    console.error("Failed to load songs:", err);
  }
}

async function selectSong(songName) {
  if (!songName) return;
  // Persist pending line edits for the song we're leaving.
  if (currentSong && songName !== currentSong && !(await flushTimings())) {
    if (!confirm("Your last lyric edits could not be saved. Switch song and discard them?")) {
      songSelect.value = currentSong;
      return;
    }
    dirtySong = null;
  }
  currentSong = songName;
  restoreSongOffset(songName); // async; applies the saved offset when it arrives
  setSaveStatus("");
  songStatusBadge.textContent = "Loading...";
  songStatusBadge.className = "status-badge";

  mainVideo.src = `/api/video/source/${encodeURIComponent(songName)}?${fmtQuery()}`;
  mainVideo.load();

  try {
    const res = await fetch(`/api/timings/${encodeURIComponent(songName)}`);
    if (res.ok) {
      const data = await res.json();
      timings = data.timings || [];
      songStatusBadge.textContent = "Timings loaded";
      songStatusBadge.className = "status-badge ready";
      renderLineList();
    } else {
      timings = [];
      lineCount.textContent = "0 lines";
      songStatusBadge.textContent = "No timings (Align needed)";
      songStatusBadge.className = "status-badge warning";
      linesList.innerHTML = '<div class="placeholder-msg">Click "Re-align" to sync lyrics with Whisper.</div>';
    }
  } catch (err) {
    timings = [];
    lineCount.textContent = "0 lines";
    songStatusBadge.textContent = "Ready to align";
  }
}

// ---------------------------------------------------------------------------
// FX knobs: controls are generated from /api/aesthetics
// ---------------------------------------------------------------------------
const fxFieldEls = {}; // field name -> { input, valueEl }
const fxToggleEls = {}; // toggle field name -> checkbox
let fxFrameTimer = null;
let fxFrameReq = 0;
let fxFrameUrl = null;
let fxFrameAbort = null;

async function initAesthetics() {
  try {
    const res = await fetch("/api/aesthetics");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    fxSchema = await res.json();
  } catch (err) {
    console.warn("Failed to load FX schema:", err);
    fxControls.innerHTML = '<div class="placeholder-msg">Could not load effect settings.</div>';
    return;
  }
  aestheticState = { ...fxSchema.defaults };
  buildFxControls();
  syncFxControls();
}

function fxDecimals(step) {
  const str = String(step);
  return str.includes(".") ? str.split(".")[1].length : 0;
}

function fxFormat(name, value) {
  return Number(value).toFixed(fxDecimals(fxSchema.fields[name].step));
}

function buildFxControls() {
  fxControls.innerHTML = "";
  fxSchema.groups.forEach((group) => {
    const names = Object.keys(fxSchema.fields).filter(
      (n) => fxSchema.fields[n].group === group.id && fxSchema.fields[n].ui
    );
    const card = document.createElement("section");
    card.className = "fx-card";
    card.innerHTML = `
      <header class="fx-card-head">
        ${group.toggle ? '<input type="checkbox" class="fx-toggle" aria-label="Enable">' : ""}
        <button type="button" class="fx-expand" aria-expanded="false">
          <span class="fx-title"></span><span class="fx-hint"></span>
        </button>
        <button type="button" class="mini fx-reset" title="Reset this effect to its defaults">↺</button>
      </header>
      <div class="fx-card-body" hidden></div>`;
    card.querySelector(".fx-title").textContent = group.label;
    card.querySelector(".fx-hint").textContent = group.hint || "";
    const body = card.querySelector(".fx-card-body");

    if (group.toggle) {
      const box = card.querySelector(".fx-toggle");
      fxToggleEls[group.toggle] = box;
      box.addEventListener("change", () => {
        aestheticState[group.toggle] = box.checked;
        scheduleFxFrame();
      });
    }
    const expand = card.querySelector(".fx-expand");
    expand.addEventListener("click", () => {
      body.hidden = !body.hidden;
      expand.setAttribute("aria-expanded", String(!body.hidden));
      card.classList.toggle("open", !body.hidden);
    });
    card.querySelector(".fx-reset").addEventListener("click", () => {
      if (group.toggle) aestheticState[group.toggle] = fxSchema.defaults[group.toggle];
      names.forEach((n) => (aestheticState[n] = fxSchema.defaults[n]));
      syncFxControls();
      scheduleFxFrame();
    });

    names.forEach((name) => {
      const spec = fxSchema.fields[name];
      const row = document.createElement("div");
      row.className = "fx-row";
      if (spec.hint) row.title = spec.hint;
      row.innerHTML = '<label><span class="fx-label"></span> <span class="fx-val"></span></label>' +
        '<input type="range">';
      row.querySelector(".fx-label").textContent = spec.label;
      const input = row.querySelector("input");
      input.min = spec.min;
      input.max = spec.max;
      input.step = spec.step;
      const valueEl = row.querySelector(".fx-val");
      fxFieldEls[name] = { input, valueEl };
      input.addEventListener("input", () => {
        const v = spec.kind === "int" ? Math.round(Number(input.value)) : Number(input.value);
        aestheticState[name] = v;
        valueEl.textContent = fxFormat(name, v);
      });
      input.addEventListener("change", scheduleFxFrame); // on release, not while dragging
      body.appendChild(row);
    });
    fxControls.appendChild(card);
  });
}

// Push aestheticState into every control.
function syncFxControls() {
  lofiMasterToggle.checked = !!aestheticState.enabled;
  Object.entries(fxToggleEls).forEach(([name, box]) => (box.checked = !!aestheticState[name]));
  Object.entries(fxFieldEls).forEach(([name, { input, valueEl }]) => {
    const v = aestheticState[name];
    input.value = v;
    valueEl.textContent = fxFormat(name, v);
  });
}

function applyAesthetics(fx) {
  aestheticState = { ...(fxSchema ? fxSchema.defaults : {}), ...fx };
  syncFxControls();
  scheduleFxFrame();
}

function scheduleFxFrame() {
  if (!currentSong) return;
  // With the safe area on the stage must always show the shrunk picture (the caption overlay is
  // placed on it), even when FX auto-refresh is off.
  if (!fxAutoRefresh.checked && !safeState.enabled) return;
  clearTimeout(fxFrameTimer);
  fxFrameTimer = setTimeout(() => (fxAutoRefresh.checked ? renderFxFrame() : renderSafeFrame()), 350);
}

function renderSafeFrame() {
  const t = stageTime();
  return showStageFx("/api/fx-frame", { time_sec: t, apply_fx: false }, `frame at ${t.toFixed(1)}s (safe area)`);
}

// The frame to show: the scrubbed time, or just inside the first line when nothing is chosen yet.
function stageTime() {
  return mainVideo.currentTime > 0.05 ? mainVideo.currentTime : (timings[0]?.start ?? 0) + 1;
}

// Run an FX request and lay the result (a JPEG frame or a GIF clip) over the video.
async function showStageFx(url, body, label) {
  if (!currentSong) return alert("Select a song first.");
  const mine = ++fxFrameReq;
  if (fxFrameAbort) fxFrameAbort.abort(); // a newer request makes the in-flight one pointless
  fxFrameAbort = new AbortController();
  fxFrameStatus.textContent = "rendering...";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        song_name: currentSong,
        format: FORMAT.id,
        aesthetics: aestheticState,
        safe_area: safePayload(),
        apply_fx: aestheticState.enabled,
        ...body,
      }),
      signal: fxFrameAbort.signal,
    });
    if (mine !== fxFrameReq) return;
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    if (mine !== fxFrameReq) return;
    if (fxFrameUrl) URL.revokeObjectURL(fxFrameUrl);
    fxFrameUrl = URL.createObjectURL(blob);
    stageFx.src = fxFrameUrl;
    stageFx.hidden = false;
    stageSafe = safeState.enabled ? { ...safeState } : null;
    fxFrameStatus.textContent = label;
    updateOverlay();
  } catch (err) {
    if (err.name === "AbortError") return; // superseded on purpose
    if (mine === fxFrameReq) fxFrameStatus.textContent = `failed: ${err.message}`;
  }
}

function renderFxFrame() {
  const t = stageTime();
  return showStageFx("/api/fx-frame", { time_sec: t }, `frame at ${t.toFixed(1)}s`);
}

function renderFxClip() {
  const t = stageTime();
  return showStageFx("/api/fx-clip", { time_sec: t, duration: 1.0 }, `clip from ${t.toFixed(1)}s`);
}

// ---------------------------------------------------------------------------
// Lyric line editor (text / start / end), auto-saved to PUT /api/timings/{song}
// ---------------------------------------------------------------------------
const NUDGE_SECONDS = 0.05;
const saveStatusEl = document.getElementById("saveStatus");
let saveTimer = null;
let saveInFlight = null;
let dirtySong = null; // song whose edits are not yet confirmed by the server
let editCounter = 0; // bumped on every edit; lets a late save response detect newer edits
let pendingRender = false; // a re-render was deferred because an input had focus
let focusAfterRender = null;

const round2 = (n) => Math.round(n * 100) / 100;

function setSaveStatus(text, cls = "") {
  if (!saveStatusEl) return;
  saveStatusEl.textContent = text;
  saveStatusEl.className = `save-status ${cls}`.trim();
}

function markDirty() {
  dirtySong = currentSong;
  editCounter += 1;
  setSaveStatus("Unsaved changes…", "pending");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveTimings, 600);
}

// Resolves true when every edit has been persisted (or there was nothing to save).
async function saveTimings() {
  clearTimeout(saveTimer);
  saveTimer = null;
  while (saveInFlight) await saveInFlight; // serialise saves
  if (!dirtySong) return true;

  const song = dirtySong;
  const sentAt = editCounter;
  dirtySong = null;
  setSaveStatus("Saving…", "pending");
  saveInFlight = (async () => {
    try {
      const res = await fetch(`/api/timings/${encodeURIComponent(song)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timings }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      // Adopt the server's normalised lines only if nothing was edited meanwhile.
      if (song === currentSong && editCounter === sentAt && !dirtySong) {
        applyServerTimings(data.timings);
      }
      if (!dirtySong) setSaveStatus("Saved", "ok");
    } catch (err) {
      dirtySong = song; // keep it dirty so the next edit / action retries
      setSaveStatus(`Save failed: ${err.message}`, "error");
    }
  })();
  try {
    await saveInFlight;
  } finally {
    saveInFlight = null;
  }
  return !dirtySong;
}

// Make sure the server has our edits before something reads them from disk.
async function flushTimings() {
  if (!dirtySong && !saveInFlight) return true;
  return saveTimings();
}

function applyServerTimings(serverTimings) {
  timings = serverTimings;
  if (linesList.contains(document.activeElement)) {
    if (linesList.querySelectorAll(".line-item").length === timings.length) syncRowValues();
    else pendingRender = true;
  } else {
    renderLineList();
  }
  updateOverlay();
}

function syncRowValues() {
  linesList.querySelectorAll(".line-item").forEach((row, i) => {
    const line = timings[i];
    if (!line) return;
    const set = (sel, value, keepIfFocused) => {
      const el = row.querySelector(sel);
      if (el && !(keepIfFocused && el === document.activeElement)) el.value = value;
    };
    // Never rewrite the text box being typed in; time fields have already committed
    // (on change), so show the server's clamped values even if still focused.
    set(".line-text-input", line.text, true);
    set(".line-start", line.start.toFixed(2), false);
    set(".line-end", line.end.toFixed(2), false);
  });
}

function buildLineRow(line, index) {
  const item = document.createElement("div");
  item.className = "line-item";
  item.dataset.index = index;
  item.innerHTML = `
    <div class="line-row-top">
      <button class="mini" data-action="play" title="Play from this line">▶</button>
      <input class="line-text-input" type="text" spellcheck="false" aria-label="Line text">
    </div>
    <div class="line-row-times">
      <span class="time-group" title="Start (seconds)">
        <button class="mini" data-action="start-" title="Start -${NUDGE_SECONDS}s">−</button>
        <input class="line-start" type="number" step="0.01" min="0" aria-label="Start seconds">
        <button class="mini" data-action="start+" title="Start +${NUDGE_SECONDS}s">+</button>
        <button class="mini" data-action="start-now" title="Set start to playhead">⌖</button>
      </span>
      <span class="time-group" title="End (seconds)">
        <button class="mini" data-action="end-" title="End -${NUDGE_SECONDS}s">−</button>
        <input class="line-end" type="number" step="0.01" min="0" aria-label="End seconds">
        <button class="mini" data-action="end+" title="End +${NUDGE_SECONDS}s">+</button>
        <button class="mini" data-action="end-now" title="Set end to playhead">⌖</button>
      </span>
      <span class="time-group row-actions">
        <button class="mini" data-action="add" title="Add a line after this one">＋</button>
        <button class="mini danger" data-action="delete" title="Delete this line">🗑</button>
      </span>
    </div>`;
  item.querySelector(".line-text-input").value = line.text;
  item.querySelector(".line-start").value = line.start.toFixed(2);
  item.querySelector(".line-end").value = line.end.toFixed(2);
  return item;
}

function renderLineList() {
  pendingRender = false;
  linesList.innerHTML = "";
  lineCount.textContent = `${timings.length} lines`;
  timings.forEach((line, index) => linesList.appendChild(buildLineRow(line, index)));
  if (focusAfterRender !== null) {
    const input = linesList.querySelector(`.line-item[data-index="${focusAfterRender}"] .line-text-input`);
    focusAfterRender = null;
    if (input) {
      input.focus();
      input.select();
    }
  }
}

function setLineTime(index, field, value) {
  const line = timings[index];
  if (!line || !Number.isFinite(value)) return;
  line[field] = Math.max(0, round2(value));
  const row = linesList.querySelector(`.line-item[data-index="${index}"]`);
  const input = row && row.querySelector(field === "start" ? ".line-start" : ".line-end");
  if (input) input.value = line[field].toFixed(2);
  markDirty();
  updateOverlay();
}

function setupLineEditor() {
  linesList.addEventListener("input", (e) => {
    if (!e.target.classList.contains("line-text-input")) return;
    const index = Number(e.target.closest(".line-item").dataset.index);
    timings[index].text = e.target.value;
    markDirty();
  });

  linesList.addEventListener("change", (e) => {
    const index = Number(e.target.closest(".line-item")?.dataset.index);
    if (e.target.classList.contains("line-start")) {
      const v = parseFloat(e.target.value);
      if (Number.isFinite(v)) setLineTime(index, "start", v);
      else e.target.value = timings[index].start.toFixed(2);
    } else if (e.target.classList.contains("line-end")) {
      const v = parseFloat(e.target.value);
      if (Number.isFinite(v)) setLineTime(index, "end", v);
      else e.target.value = timings[index].end.toFixed(2);
    }
  });

  linesList.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const index = Number(btn.closest(".line-item").dataset.index);
    const line = timings[index];
    const now = mainVideo.currentTime || 0;
    switch (btn.dataset.action) {
      case "play": // show this line: jump to its middle and refresh the frame
        mainVideo.currentTime = Math.max(0, (line.start + line.end) / 2 + lyricOffset());
        break;
      case "start-": setLineTime(index, "start", line.start - NUDGE_SECONDS); break;
      case "start+": setLineTime(index, "start", line.start + NUDGE_SECONDS); break;
      case "end-": setLineTime(index, "end", line.end - NUDGE_SECONDS); break;
      case "end+": setLineTime(index, "end", line.end + NUDGE_SECONDS); break;
      case "start-now": setLineTime(index, "start", now - lyricOffset()); break;
      case "end-now": setLineTime(index, "end", now - lyricOffset()); break;
      case "add": {
        const next = timings[index + 1];
        const gap = next ? next.start - line.end : 2;
        const MIN_NEW = 0.5;
        let start = round2(line.end);
        let length = Math.min(2, Math.max(gap, MIN_NEW));
        if (gap < MIN_NEW) {
          // Back-to-back lines: take the room from the end of this line so neighbouring
          // lines don't get pushed around by the server's overlap resolution.
          const room = Math.min(MIN_NEW, Math.max(0, (line.end - line.start) / 2));
          line.end = round2(line.end - room);
          start = line.end;
          length = Math.max(room, 0.1);
        }
        timings.splice(index + 1, 0, {
          text: "new line",
          start,
          end: round2(start + length),
          words: [],
        });
        focusAfterRender = index + 1;
        renderLineList();
        markDirty();
        break;
      }
      case "delete":
        if (!confirm(`Delete this line?\n\n"${line.text}"`)) return;
        timings.splice(index, 1);
        renderLineList();
        markDirty();
        break;
    }
  });

  // Re-render deferred while typing once focus leaves the list.
  linesList.addEventListener("focusout", () => {
    setTimeout(() => {
      if (pendingRender && !linesList.contains(document.activeElement)) renderLineList();
    }, 0);
  });

  window.addEventListener("beforeunload", (e) => {
    if (dirtySong || saveInFlight) e.preventDefault();
  });
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// Where the caption sits, in the format's reference render space: the explicit drag/slider position when set,
// otherwise the alignment grid + bottom margin (same rules the overlay has always used).
function effectivePos() {
  if (styleState.pos_x !== null && styleState.pos_y !== null) {
    return { x: styleState.pos_x, y: styleState.pos_y };
  }
  const al = styleState.alignment;
  let x = RW / 2;
  if ([7, 4, 1].includes(al)) x = 0.15 * RW;
  else if ([9, 6, 3].includes(al)) x = 0.85 * RW;
  let y;
  if ([7, 8, 9].includes(al)) y = 0.15 * RH;
  else if ([4, 5, 6].includes(al)) y = 0.5 * RH;
  else y = RH - styleState.margin_v;
  return { x: Math.round(x), y: Math.round(y) };
}

// The renderer only honours pos_x/pos_y when BOTH are set, so moving one slider must seed the other
// from where the caption currently is (otherwise the first slider does nothing).
function seedCustomPosition() {
  if (styleState.pos_x !== null && styleState.pos_y !== null) return;
  const cur = effectivePos();
  if (styleState.pos_x === null) styleState.pos_x = cur.x;
  if (styleState.pos_y === null) styleState.pos_y = cur.y;
}

// Write the position-related controls from styleState (sliders, labels, alignment buttons).
function syncPositionControls() {
  const custom = styleState.pos_x !== null && styleState.pos_y !== null;
  const pos = effectivePos();
  posX.value = pos.x;
  posY.value = pos.y;
  const al = styleState.alignment;
  const gridX = [7, 4, 1].includes(al) ? "Left" : [9, 6, 3].includes(al) ? "Right" : "Center";
  posXVal.textContent = custom ? `${pos.x}px` : gridX;
  const gridY = [7, 8, 9].includes(al) ? "Top" : [4, 5, 6].includes(al) ? "Middle" : "Bottom";
  posYVal.textContent = custom ? `${pos.y}px` : gridY;
  marginV.value = styleState.margin_v;
  marginVVal.textContent = styleState.margin_v;
  document.querySelectorAll(".btn-align").forEach((b) => {
    b.classList.toggle("active", parseInt(b.dataset.align, 10) === styleState.alignment);
  });
}

// Live Subtitle Overlay Renderer
// ---------------------------------------------------------------------------
// Keep words whole (mirror of plan_line_fit in lyrics_sync.py, in reference script pixels).
// A word is only cut when it is wider than the text column. Order of preference: widen the column
// for that line, then shrink the font, and only as a last resort split the word with a trailing "-".
// ---------------------------------------------------------------------------
const FIT = { minScale: 0.6, minSide: 30, safety: 1.03 };
const fitCtx = document.createElement("canvas").getContext("2d");

function measureScript(text, size) {
  fitCtx.font = `${styleState.italic ? "italic " : ""}${styleState.bold ? "bold " : ""}${size}px ` +
    `"${styleState.font_name}", Helvetica, Arial, sans-serif`;
  return fitCtx.measureText(text).width + (styleState.letter_spacing || 0) * text.length;
}

function splitWord(word, size, maxWidth) {
  const chunks = [];
  let rest = word;
  while (rest.length > 2 && measureScript(rest, size) > maxWidth) {
    let n = rest.length - 1;
    while (n > 1 && measureScript(rest.slice(0, n) + "-", size) > maxWidth) n--;
    chunks.push(rest.slice(0, n) + "-");
    rest = rest.slice(n);
  }
  chunks.push(rest);
  return chunks;
}

function baseMargins() {
  if (styleState.box_width) {
    const side = Math.max(10, Math.floor((RW - Number(styleState.box_width)) / 2));
    return [side, side];
  }
  return [styleState.margin_l ?? 70, styleState.margin_r ?? 70];
}

function planLineFit(words) {
  const [ml, mr] = baseMargins();
  const plan = { margins: [ml, mr], fontSize: styleState.font_size, chunks: {} };
  const widths = words.map((w) => measureScript(w, styleState.font_size) * FIT.safety);
  const widest = Math.max(0, ...widths);
  if (widest <= RW - ml - mr) return plan;

  const maxAvail = RW - 2 * FIT.minSide;
  if (widest <= maxAvail) {
    const side = Math.max(FIT.minSide, Math.floor((RW - widest) / 2));
    plan.margins = [Math.min(ml, side), Math.min(mr, side)];
    return plan;
  }
  plan.margins = [FIT.minSide, FIT.minSide];
  const scale = maxAvail / widest;
  if (scale >= FIT.minScale) {
    plan.fontSize = Math.max(1, Math.floor(styleState.font_size * scale * 0.98));
    return plan;
  }
  words.forEach((w, i) => {
    if (widths[i] > maxAvail) plan.chunks[i] = splitWord(w, styleState.font_size, maxAvail / FIT.safety);
  });
  return plan;
}

function updateOverlay() {
  if (!mainVideo.videoWidth) return;
  // videoWidth is the intrinsic size; the element can still be displayed at 0x0 (e.g. while the
  // layout is being squeezed). Hide rather than compute coordinates from a zero-size box.
  if (!mainVideo.clientWidth || !mainVideo.clientHeight) {
    subOverlay.style.display = "none";
    return;
  }

  // Lay the FX image exactly over the (hidden-behind) source frame.
  stageFx.style.left = `${mainVideo.offsetLeft}px`;
  stageFx.style.top = `${mainVideo.offsetTop}px`;
  stageFx.style.width = `${mainVideo.clientWidth}px`;
  stageFx.style.height = `${mainVideo.clientHeight}px`;
  reelsOverlay.style.left = stageFx.style.left;
  reelsOverlay.style.top = stageFx.style.top;
  reelsOverlay.style.width = stageFx.style.width;
  reelsOverlay.style.height = stageFx.style.height;

  // This is a still frame used to place the caption, so show the closest line even in a gap.
  const t = mainVideo.currentTime - lyricOffset(); // lyric time
  const distance = (l) => (t < l.start ? l.start - t : t > l.end ? t - l.end : 0);
  const currentLine = timings.reduce((best, l) => (!best || distance(l) < distance(best) ? l : best), null);

  if (!currentLine) {
    subOverlay.style.display = "none";
    return;
  }

  subOverlay.style.display = "block";

  const fmt = (str) => styleState.lowercase ? (str || "").toLowerCase() : (str || "");
  const useKaraoke = styleState.karaoke && currentLine.words && currentLine.words.length;
  const tokens = useKaraoke ? currentLine.words.map((w) => fmt(w.text)) : fmt(currentLine.text).split(/\s+/).filter(Boolean);
  const plan = planLineFit(tokens);

  // Typography
  subOverlay.style.fontFamily = `"${styleState.font_name}", Helvetica, Arial, sans-serif`;
  subOverlay.style.fontWeight = styleState.bold ? "bold" : "normal";
  subOverlay.style.fontStyle = styleState.italic ? "italic" : "normal";
  subOverlay.style.letterSpacing = `${styleState.letter_spacing}px`;
  subOverlay.style.opacity = styleState.text_opacity !== undefined ? styleState.text_opacity : 0.88;

  // Scale font relative to the displayed height vs the format's reference height
  const vidHeight = mainVideo.clientHeight || videoContainer.clientHeight;
  const scaleRatio = vidHeight / RH;
  const computedFontSize = Math.max(3, plan.fontSize * scaleRatio);
  subOverlay.style.fontSize = `${computedFontSize}px`;

  // Scale subtitle box width to force blocky line wrapping
  const vidWidth = mainVideo.clientWidth || videoContainer.clientWidth;
  const scaleRatioW = vidWidth / RW;
  subOverlay.style.maxWidth = `${Math.max(40, (RW - plan.margins[0] - plan.margins[1]) * scaleRatioW)}px`;

  // Outline, Shadow & Chromatic simulation
  const strokePx = Math.max(0.4, styleState.outline * scaleRatio * 1.5);
  const shadowPx = styleState.shadow * scaleRatio * 2;
  subOverlay.style.webkitTextStroke = `${strokePx}px ${styleState.outline_color}`;

  let shadowStr = `${shadowPx}px ${shadowPx}px 0px ${styleState.outline_color}`;
  if (aestheticState.enabled && aestheticState.chromatic_aberration && (aestheticState.ca_shift_px ?? 3) > 0) {
    const off = ((aestheticState.ca_shift_px ?? 3) * 2) / 3; // 3px in the render ~ 2px in the player
    shadowStr += `, -${off}px 0 rgba(255, 30, 80, 0.65), ${off}px 0 rgba(0, 150, 255, 0.65)`;
  }
  subOverlay.style.textShadow = shadowStr;

  // Blur
  const filters = [];
  if (styleState.blur > 0) {
    filters.push(`blur(${styleState.blur}px)`);
  }
  subOverlay.style.filter = filters.length ? filters.join(" ") : "none";

  // Positioning
  const vidRect = mainVideo.getBoundingClientRect();
  const contRect = videoContainer.getBoundingClientRect();

  const offsetX = vidRect.left - contRect.left;
  const offsetY = vidRect.top - contRect.top;

  let pos = effectivePos();
  if (stageSafe) {
    // The picture on the stage is shrunk into the safe area, and the render moves the captions
    // with it (size unchanged), so show them at the mapped spot.
    const g = safeGeometry(stageSafe);
    pos = { x: g.x0 + pos.x * g.s, y: g.y0 + pos.y * g.s };
  }
  const xPct = (pos.x / RW) * 100;
  const yPct = (pos.y / RH) * 100;

  subOverlay.style.left = `${offsetX + (vidWidth * xPct) / 100}px`;
  subOverlay.style.top = `${offsetY + (vidHeight * yPct) / 100}px`;
  subOverlay.style.transform = "translate(-50%, -50%)";

  // Karaoke and lowercase rendering. Words are separated by real spaces so the browser wraps
  // between words (like the renderer) instead of inside them.
  const pieces = (i, text) => (plan.chunks[i] || [text]).map(escapeHtml).join("<br>");

  if (useKaraoke) {
    subText.innerHTML = currentLine.words.map((w, i) => {
      const col = t >= w.start ? styleState.primary_color : styleState.secondary_color;
      return `<span style="color: ${col};">${pieces(i, tokens[i])}</span>`;
    }).join(" ");
  } else {
    subText.innerHTML = tokens.map((tok, i) => pieces(i, tok)).join(" ");
    subText.style.color = styleState.secondary_color;
  }

  // Highlight line in line list
  const activeIdx = timings.indexOf(currentLine);
  document.querySelectorAll(".line-item").forEach((el, idx) => {
    if (idx === activeIdx) {
      const becameActive = !el.classList.contains("active");
      el.classList.add("active");
      // Only follow playback; don't yank the list while the user is editing in it.
      if (becameActive && !linesList.contains(document.activeElement)) {
        el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } else {
      el.classList.remove("active");
    }
  });
}

// Drag overlay to reposition in video
function setupDragReposition() {
  let isDragging = false;

  subOverlay.addEventListener("mousedown", (e) => {
    isDragging = true;
    subOverlay.classList.add("active-drag");
    e.preventDefault();
  });

  window.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    const vidRect = mainVideo.getBoundingClientRect();
    if (!vidRect.width || !vidRect.height) return;

    const relX = Math.max(0, Math.min(vidRect.width, e.clientX - vidRect.left));
    const relY = Math.max(0, Math.min(vidRect.height, e.clientY - vidRect.top));

    // Convert to the reference canvas
    let refX = (relX / vidRect.width) * RW;
    let refY = (relY / vidRect.height) * RH;
    if (stageSafe) {
      // Pointer is on the shrunk picture: convert back to the full-frame coordinates the style uses.
      const g = safeGeometry(stageSafe);
      refX = (refX - g.x0) / g.s;
      refY = (refY - g.y0) / g.s;
    }
    refX = Math.round(Math.max(0, Math.min(RW, refX)));
    refY = Math.round(Math.max(0, Math.min(RH, refY)));

    styleState.pos_x = refX;
    styleState.pos_y = refY;
    syncPositionControls();

    updateOverlay();
  });

  window.addEventListener("mouseup", () => {
    if (isDragging) {
      isDragging = false;
      subOverlay.classList.remove("active-drag");
    }
  });
}

function setupEventListeners() {
  songSelect.addEventListener("change", (e) => selectSong(e.target.value));

  mainVideo.addEventListener("timeupdate", () => {
    if (!mainVideo.duration) return;
    timeDisplay.textContent = `${formatTime(mainVideo.currentTime)} / ${formatTime(mainVideo.duration)}`;
    timelineSlider.value = (mainVideo.currentTime / mainVideo.duration) * 100;
    updateOverlay();
  });

  timelineSlider.addEventListener("input", (e) => {
    if (mainVideo.duration) {
      mainVideo.currentTime = (e.target.value / 100) * mainVideo.duration;
      updateOverlay();
    }
  });
  // A new frame makes the FX picture stale: drop it, and re-render it when auto-refresh is on.
  mainVideo.addEventListener("loadeddata", scheduleFxFrame);
  mainVideo.addEventListener("seeked", () => {
    hideStageFx();
    scheduleFxFrame();
  });

  // Typography inputs
  fontInput.addEventListener("input", (e) => {
    styleState.font_name = e.target.value || "Helvetica";
    updateOverlay();
  });

  fontSize.addEventListener("input", (e) => {
    styleState.font_size = parseInt(e.target.value, 10);
    fontSizeVal.textContent = e.target.value;
    updateOverlay();
  });

  boxWidth.addEventListener("input", (e) => {
    styleState.box_width = parseInt(e.target.value, 10);
    boxWidthVal.textContent = e.target.value;
    updateOverlay();
  });

  fontBold.addEventListener("change", (e) => {
    styleState.bold = e.target.checked;
    updateOverlay();
  });

  fontItalic.addEventListener("change", (e) => {
    styleState.italic = e.target.checked;
    updateOverlay();
  });

  fontLowercase.addEventListener("change", (e) => {
    styleState.lowercase = e.target.checked;
    updateOverlay(); // the line editor shows raw text, so it needs no re-render (that stole the caret)
  });

  letterSpacing.addEventListener("input", (e) => {
    styleState.letter_spacing = parseInt(e.target.value, 10) || 0;
    updateOverlay();
  });

  // Colors & Opacity
  primaryColor.addEventListener("input", (e) => {
    styleState.primary_color = e.target.value;
    updateOverlay();
  });

  secondaryColor.addEventListener("input", (e) => {
    styleState.secondary_color = e.target.value;
    updateOverlay();
  });

  outlineColor.addEventListener("input", (e) => {
    styleState.outline_color = e.target.value;
    updateOverlay();
  });

  textOpacity.addEventListener("input", (e) => {
    styleState.text_opacity = parseInt(e.target.value, 10) / 100.0;
    textOpacityVal.textContent = e.target.value;
    updateOverlay();
  });

  outlineWidth.addEventListener("input", (e) => {
    styleState.outline = parseFloat(e.target.value);
    outlineVal.textContent = e.target.value;
    updateOverlay();
  });

  shadowDepth.addEventListener("input", (e) => {
    styleState.shadow = parseFloat(e.target.value);
    shadowVal.textContent = e.target.value;
    updateOverlay();
  });

  blurAmount.addEventListener("input", (e) => {
    styleState.blur = parseFloat(e.target.value);
    blurVal.textContent = e.target.value;
    updateOverlay();
  });

  // Alignment buttons
  document.querySelectorAll(".btn-align").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".btn-align").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      styleState.alignment = parseInt(btn.dataset.align, 10);
      styleState.pos_x = null;
      styleState.pos_y = null;
      syncPositionControls();
      updateOverlay();
    });
  });

  marginV.addEventListener("input", (e) => {
    styleState.margin_v = parseInt(e.target.value, 10);
    styleState.pos_x = null;
    styleState.pos_y = null;
    syncPositionControls();
    updateOverlay();
  });

  posX.addEventListener("input", (e) => {
    seedCustomPosition();
    styleState.pos_x = parseInt(e.target.value, 10);
    syncPositionControls();
    updateOverlay();
  });

  posY.addEventListener("input", (e) => {
    seedCustomPosition();
    styleState.pos_y = parseInt(e.target.value, 10);
    syncPositionControls();
    updateOverlay();
  });

  // Global subtitle offset
  timeOffset.addEventListener("input", (e) => setTimeOffset(e.target.value));
  offsetMinus.addEventListener("click", () => setTimeOffset(lyricOffset() - 0.1));
  offsetPlus.addEventListener("click", () => setTimeOffset(lyricOffset() + 0.1));
  offsetReset.addEventListener("click", () => setTimeOffset(0));

  // Motion
  fadeIn.addEventListener("input", (e) => {
    styleState.fade_in_ms = parseInt(e.target.value, 10);
    fadeInVal.textContent = e.target.value;
  });

  fadeOut.addEventListener("input", (e) => {
    styleState.fade_out_ms = parseInt(e.target.value, 10);
    fadeOutVal.textContent = e.target.value;
  });

  opacityTrailing.addEventListener("change", (e) => {
    styleState.opacity_trailing = e.target.checked;
  });

  karaokeToggle.addEventListener("change", (e) => {
    styleState.karaoke = e.target.checked;
    updateOverlay();
  });

  karaokeTag.addEventListener("change", (e) => {
    styleState.karaoke_tag = e.target.value;
  });

  // Aesthetic Toggles
  lofiMasterToggle.addEventListener("change", (e) => {
    aestheticState.enabled = e.target.checked;
    scheduleFxFrame();
  });

  btnFxFrame.addEventListener("click", renderFxFrame);
  btnFxClip.addEventListener("click", renderFxClip);
  btnFxOriginal.addEventListener("click", hideStageFx);
  btnFxResetAll.addEventListener("click", () => {
    if (!fxSchema) return;
    const enabled = aestheticState.enabled;
    aestheticState = { ...fxSchema.defaults, enabled };
    syncFxControls();
    scheduleFxFrame();
  });

  // Presets load/save
  presetSelect.addEventListener("change", (e) => {
    const p = presets[e.target.value];
    updatePresetButtons();
    if (!p) return;
    if (p.format && p.format !== FORMAT.id && p.style) {
      const label = (formats.find((f) => f.id === p.format) || { label: p.format }).label;
      if (!confirm(`This preset's caption layout was made for ${label}, but you are editing ${FORMAT.label}. Apply it anyway?`)) return;
    }
    // A preset holds a caption style, FX settings, or both; apply whichever it has.
    if (p.style) applyStyle(p.style);
    if (p.aesthetics) applyAesthetics(p.aesthetics);
  });

  btnSavePreset.addEventListener("click", async () => {
    const current = presets[presetSelect.value];
    // Defaulting to the selected user preset's name makes "Save As" double as "update this preset".
    const suggestion = current && !current.builtin ? presetSelect.value : "My Custom Style";
    const name = (prompt("Name this preset (saves caption style + FX settings):", suggestion) || "").trim();
    if (!name) return;
    if (presets[name]?.builtin) {
      return alert(`"${name}" is a built-in preset and can't be changed. Pick another name.`);
    }
    if (presets[name] && !confirm(`A preset named "${name}" already exists. Replace it with the current settings?`)) {
      return;
    }
    const ok = await presetRequest(`/api/presets/${encodeURIComponent(name)}`, "POST", {
      style: Object.fromEntries(Object.entries(styleState).filter(([k]) => k !== "time_offset")),
      aesthetics: aestheticState,
      format: FORMAT.id,
    });
    if (ok) await loadPresets(name);
  });

  btnRenamePreset.addEventListener("click", async () => {
    const old = presetSelect.value;
    if (!presets[old] || presets[old].builtin) return;
    const name = (prompt(`Rename "${old}" to:`, old) || "").trim();
    if (!name || name === old) return;
    if (await presetRequest(`/api/presets/${encodeURIComponent(old)}/rename`, "POST", { new_name: name })) {
      await loadPresets(name);
    }
  });

  btnDeletePreset.addEventListener("click", async () => {
    const name = presetSelect.value;
    if (!presets[name] || presets[name].builtin) return;
    if (!confirm(`Delete the preset "${name}"? This can't be undone.`)) return;
    if (await presetRequest(`/api/presets/${encodeURIComponent(name)}`, "DELETE")) {
      await loadPresets(Object.keys(presets).find((n) => n !== name));
    }
  });

  // Job Triggers
  btnRealign.addEventListener("click", async () => {
    if (!currentSong) return alert("Select a song first.");
    if (
      !confirm(
        `Run Whisper alignment for "${currentSong}"?\n\n` +
          "This replaces the current line timings, including any manual edits " +
          "(the original alignment is kept once as a .bak file next to the lyrics .json)."
      )
    ) {
      return;
    }
    dirtySong = null; // discard unsaved edits; they would be overwritten anyway
    clearTimeout(saveTimer);
    startJob("/api/align", { song_name: currentSong, format: FORMAT.id }, "Whisper Alignment", () => {
      selectSong(currentSong);
    });
  });

  btnPreview10s.addEventListener("click", async () => {
    if (!currentSong) return alert("Select a song first.");
    if (!(await flushTimings())) return alert("Lyric edits could not be saved; fix the error first.");
    const startSec = pickPreviewStart();
    if (
      timings.length &&
      !windowHasLyrics(startSec, PREVIEW_SECONDS) &&
      !confirm(
        `No lyric lines between ${formatTime(startSec)} and ${formatTime(startSec + PREVIEW_SECONDS)}, ` +
          "so the preview will have no captions. Render anyway?"
      )
    ) {
      return;
    }
    startJob(
      "/api/preview",
      {
        song_name: currentSong,
        format: FORMAT.id,
        style: styleState,
        aesthetics: aestheticState,
        apply_lofi: aestheticState.enabled,
        safe_area: safePayload(),
        start_sec: startSec,
        duration_sec: PREVIEW_SECONDS,
      },
      "10-Second Lo-Fi Preview",
      (job) => {
        if (job.output_url) {
          // Show the result in its own player; mainVideo stays on the source so the
          // timeline, line list and the next preview's start time remain song-relative.
          previewVideo.src = job.output_url;
          previewPanel.hidden = false;
          previewVideo.play().catch(() => {});
        }
      }
    );
  });


  btnRenderFull.addEventListener("click", async () => {
    if (!currentSong) return alert("Select a song first.");
    if (!confirm(`Render full Lo-Fi video for "${currentSong}" with audio-reactive effects?`)) return;
    if (!(await flushTimings())) return alert("Lyric edits could not be saved; fix the error first.");
    startJob(
      "/api/render",
      {
        song_name: currentSong,
        format: FORMAT.id,
        style: styleState,
        aesthetics: aestheticState,
        lofi_mode: true,
        safe_area: safePayload(),
      },
      "Full Lo-Fi Emo-Trap Render",
      () => {
        alert("Render completed! Video saved to sm_content/");
      }
    );
  });

  btnRenderClean.addEventListener("click", async () => {
    if (!currentSong) return alert("Select a song first.");
    if (!confirm(`Render clean (fast) video for "${currentSong}"?`)) return;
    if (!(await flushTimings())) return alert("Lyric edits could not be saved; fix the error first.");
    startJob(
      "/api/render",
      {
        song_name: currentSong,
        format: FORMAT.id,
        style: styleState,
        lofi_mode: false,
        safe_area: safePayload(),
      },
      "Full Clean Video Render",
      () => {
        alert("Clean render completed! Video saved to sm_content/");
      }
    );
  });

  btnCancelJob.addEventListener("click", async () => {
    if (activeJobId) {
      await fetch(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
    }
  });
}

function applyStyle(st) {
  // The offset belongs to the song, not to a look: presets must not overwrite it.
  const { time_offset: _ignored, ...look } = st || {};
  Object.assign(styleState, look);
  fontInput.value = styleState.font_name || "Helvetica";
  fontSize.value = styleState.font_size;
  fontSizeVal.textContent = styleState.font_size;
  boxWidth.value = styleState.box_width || 680;
  boxWidthVal.textContent = styleState.box_width || 680;
  fontBold.checked = styleState.bold;
  fontItalic.checked = styleState.italic;
  fontLowercase.checked = styleState.lowercase !== false;
  letterSpacing.value = styleState.letter_spacing || 0;
  primaryColor.value = styleState.primary_color;
  secondaryColor.value = styleState.secondary_color;
  outlineColor.value = styleState.outline_color;
  textOpacity.value = Math.round((styleState.text_opacity !== undefined ? styleState.text_opacity : 0.88) * 100);
  textOpacityVal.textContent = textOpacity.value;
  outlineWidth.value = styleState.outline;
  outlineVal.textContent = styleState.outline;
  shadowDepth.value = styleState.shadow;
  shadowVal.textContent = styleState.shadow;
  marginV.value = styleState.margin_v;
  marginVVal.textContent = styleState.margin_v;
  fadeIn.value = styleState.fade_in_ms;
  fadeInVal.textContent = styleState.fade_in_ms;
  fadeOut.value = styleState.fade_out_ms;
  fadeOutVal.textContent = styleState.fade_out_ms;
  opacityTrailing.checked = styleState.opacity_trailing !== false;
  blurAmount.value = styleState.blur || 0;
  blurVal.textContent = styleState.blur || 0;
  karaokeToggle.checked = styleState.karaoke;
  karaokeTag.value = styleState.karaoke_tag || "kf";
  setTimeOffset(styleState.time_offset, { persist: false });
  syncPositionControls();
  updateOverlay();
}

async function startJob(url, payload, title, onComplete) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to start job");

    activeJobId = data.job_id;
    jobCard.style.display = "block";
    jobTitle.textContent = title;
    jobProgressBar.style.width = "5%";
    jobMessage.textContent = "Queued...";
    jobMessage.style.color = "";
    jobOutputLinks.innerHTML = "";
    setJobButtonsDisabled(true);

    if (activeJobPollInterval) clearInterval(activeJobPollInterval);
    const jobId = activeJobId;
    let failedPolls = 0;
    let polling = false;
    const stopPolling = () => {
      clearInterval(activeJobPollInterval);
      activeJobPollInterval = null;
      setJobButtonsDisabled(false);
    };
    activeJobPollInterval = setInterval(async () => {
      if (polling) return; // don't overlap requests
      polling = true;
      try {
        const jRes = await fetch(`/api/jobs/${jobId}`);
        if (!jRes.ok) throw new Error(`HTTP ${jRes.status}`);
        failedPolls = 0;
        const job = await jRes.json();

        jobProgressBar.style.width = `${Math.min(100, Math.max(5, job.progress))}%`;
        jobMessage.textContent = job.message;

        if (job.status === "completed") {
          stopPolling();
          jobMessage.textContent = "Done!";
          if (job.output_url) {
            jobOutputLinks.innerHTML = `<a href="${job.output_url}" target="_blank">Download Video</a>`;
          }
          if (onComplete) onComplete(job);
        } else if (job.status === "failed" || job.status === "cancelled") {
          stopPolling();
          jobMessage.textContent = job.error || "Job stopped.";
          jobMessage.style.color = "var(--accent-danger)";
        }
      } catch (err) {
        // Server restarted / job id lost: give up instead of polling forever.
        if (++failedPolls >= 5) {
          stopPolling();
          jobMessage.textContent = `Lost contact with the job (${err.message}).`;
          jobMessage.style.color = "var(--accent-danger)";
        }
      } finally {
        polling = false;
      }
    }, 800);
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

function formatTime(seconds) {
  if (isNaN(seconds) || seconds === null) return "00:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m < 10 ? "0" : ""}${m}:${s < 10 ? "0" : ""}${s}`;
}

function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
