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
};

// Lo-Fi Emo-Trap Aesthetic State (All checked by default)
let aestheticState = {
  enabled: true,
  el_cocuy_wash: true,
  framerate_decimate: true,
  target_fps: 15,
  iso_noise: true,
  noise_strength: 0.35,
  chromatic_aberration: true,
  fogged_glass_vignette: true,
  crt_scanlines: true,
  sidechain_pump: true,
  fuzzed_glitch: true,
};

// DOM elements
const songSelect = document.getElementById("songSelect");
const songStatusBadge = document.getElementById("songStatusBadge");
const btnRealign = document.getElementById("btnRealign");
const mainVideo = document.getElementById("mainVideo");
const subOverlay = document.getElementById("subOverlay");
const subText = document.getElementById("subText");
const videoContainer = document.getElementById("videoContainer");
const lofiScanlinesLayer = document.getElementById("lofiScanlinesLayer");
const lofiFogLayer = document.getElementById("lofiFogLayer");

const btnPlay = document.getElementById("btnPlay");
const timeDisplay = document.getElementById("timeDisplay");
const timelineSlider = document.getElementById("timelineSlider");
const linesList = document.getElementById("linesList");
const lineCount = document.getElementById("lineCount");

// Typography & Layout elements
const presetSelect = document.getElementById("presetSelect");
const btnSavePreset = document.getElementById("btnSavePreset");
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
const fadeOut = document.getElementById("fadeOut");
const fadeOutVal = document.getElementById("fadeOutVal");
const opacityTrailing = document.getElementById("opacityTrailing");
const karaokeToggle = document.getElementById("karaokeToggle");
const karaokeTag = document.getElementById("karaokeTag");

// Aesthetic toggles
const lofiMasterToggle = document.getElementById("lofiMasterToggle");
const fxElCocuy = document.getElementById("fxElCocuy");
const fxDecimate = document.getElementById("fxDecimate");
const fxIsoNoise = document.getElementById("fxIsoNoise");
const fxChromatic = document.getElementById("fxChromatic");
const fxFoggedGlass = document.getElementById("fxFoggedGlass");
const fxScanlines = document.getElementById("fxScanlines");
const fxSidechain = document.getElementById("fxSidechain");
const fxGlitch = document.getElementById("fxGlitch");

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

let activeJobId = null;

// Initialize app
document.addEventListener("DOMContentLoaded", async () => {
  await loadFonts();
  await loadPresets();
  await loadSongs();
  setupEventListeners();
  setupDragReposition();
  updateLofiPreviewCSS();
});

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

async function loadPresets() {
  try {
    const res = await fetch("/api/presets");
    presets = await res.json();
    presetSelect.innerHTML = "";
    Object.keys(presets).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      presetSelect.appendChild(opt);
    });
  } catch (err) {
    console.warn("Failed to load presets:", err);
  }
}

async function loadSongs() {
  try {
    const res = await fetch("/api/songs");
    const data = await res.json();
    songSelect.innerHTML = '<option value="">-- Choose Song --</option>';
    data.songs.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.name + (s.video_path ? "" : " (No video)");
      opt.disabled = !s.video_path;
      songSelect.appendChild(opt);
    });

    const firstPlayable = data.songs.find((s) => s.video_path);
    if (firstPlayable) {
      songSelect.value = firstPlayable.name;
      selectSong(firstPlayable.name);
    }
  } catch (err) {
    console.error("Failed to load songs:", err);
  }
}

async function selectSong(songName) {
  if (!songName) return;
  currentSong = songName;
  songStatusBadge.textContent = "Loading...";
  songStatusBadge.className = "status-badge";

  mainVideo.src = `/api/video/source/${encodeURIComponent(songName)}`;
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
      songStatusBadge.textContent = "No timings (Align needed)";
      songStatusBadge.className = "status-badge warning";
      linesList.innerHTML = '<div class="placeholder-msg">Click "Re-align" to sync lyrics with Whisper.</div>';
    }
  } catch (err) {
    timings = [];
    songStatusBadge.textContent = "Ready to align";
  }
}

function renderLineList() {
  linesList.innerHTML = "";
  lineCount.textContent = `${timings.length} lines`;

  timings.forEach((line, index) => {
    const item = document.createElement("div");
    item.className = "line-item";
    item.dataset.index = index;
    const txt = styleState.lowercase ? line.text.toLowerCase() : line.text;
    item.innerHTML = `
      <span class="line-text">${escapeHtml(txt)}</span>
      <span class="line-time">${formatTime(line.start)}</span>
    `;
    item.addEventListener("click", () => {
      mainVideo.currentTime = Math.max(0, line.start - 0.2);
      mainVideo.play();
    });
    linesList.appendChild(item);
  });
}

function updateLofiPreviewCSS() {
  const lofiActive = aestheticState.enabled;
  if (lofiScanlinesLayer) {
    lofiScanlinesLayer.classList.toggle("active", lofiActive && aestheticState.crt_scanlines);
  }
  if (lofiFogLayer) {
    lofiFogLayer.classList.toggle("active", lofiActive && aestheticState.fogged_glass_vignette);
  }
  if (mainVideo) {
    mainVideo.classList.toggle("lofi-wash-active", lofiActive && aestheticState.el_cocuy_wash);
  }
  updateOverlay();
}

// Live Subtitle Overlay Renderer
function updateOverlay() {
  if (!mainVideo.videoWidth) return;

  const t = mainVideo.currentTime;
  const currentLine = timings.find((l) => t >= l.start - 0.15 && t <= l.end + 0.35);

  if (!currentLine) {
    subOverlay.style.display = "none";
    return;
  }

  subOverlay.style.display = "block";

  // Typography
  subOverlay.style.fontFamily = `"${styleState.font_name}", Helvetica, Arial, sans-serif`;
  subOverlay.style.fontWeight = styleState.bold ? "bold" : "normal";
  subOverlay.style.fontStyle = styleState.italic ? "italic" : "normal";
  subOverlay.style.letterSpacing = `${styleState.letter_spacing}px`;
  subOverlay.style.opacity = styleState.text_opacity !== undefined ? styleState.text_opacity : 0.88;

  // Scale font relative to container height vs 1920 reference video
  const vidHeight = mainVideo.clientHeight || videoContainer.clientHeight;
  const scaleRatio = vidHeight / 1920;
  const computedFontSize = Math.max(12, styleState.font_size * scaleRatio);
  subOverlay.style.fontSize = `${computedFontSize}px`;

  // Scale subtitle box width to force blocky line wrapping
  const vidWidth = mainVideo.clientWidth || videoContainer.clientWidth;
  if (styleState.box_width) {
    const scaleRatioW = vidWidth / 1080;
    const computedMaxW = Math.max(160, styleState.box_width * scaleRatioW);
    subOverlay.style.maxWidth = `${computedMaxW}px`;
  } else {
    subOverlay.style.maxWidth = "90%";
  }

  // Outline, Shadow & Chromatic simulation
  const strokePx = Math.max(1, styleState.outline * scaleRatio * 1.5);
  const shadowPx = styleState.shadow * scaleRatio * 2;
  subOverlay.style.webkitTextStroke = `${strokePx}px ${styleState.outline_color}`;

  let shadowStr = `${shadowPx}px ${shadowPx}px 0px ${styleState.outline_color}`;
  if (aestheticState.enabled && aestheticState.chromatic_aberration) {
    shadowStr += `, -2px 0 rgba(255, 30, 80, 0.65), 2px 0 rgba(0, 150, 255, 0.65)`;
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

  let xPct = 50;
  let yPct = 80;

  if (styleState.pos_x !== null && styleState.pos_y !== null) {
    xPct = (styleState.pos_x / 1080) * 100;
    yPct = (styleState.pos_y / 1920) * 100;
  } else {
    const al = styleState.alignment;
    if ([7, 4, 1].includes(al)) xPct = 15;
    else if ([9, 6, 3].includes(al)) xPct = 85;
    else xPct = 50;

    if ([7, 8, 9].includes(al)) yPct = 15;
    else if ([4, 5, 6].includes(al)) yPct = 50;
    else yPct = ((1920 - styleState.margin_v) / 1920) * 100;
  }

  subOverlay.style.left = `${offsetX + (vidWidth * xPct) / 100}px`;
  subOverlay.style.top = `${offsetY + (vidHeight * yPct) / 100}px`;
  subOverlay.style.transform = "translate(-50%, -50%)";

  // Karaoke and lowercase rendering
  const fmt = (str) => styleState.lowercase ? (str || "").toLowerCase() : (str || "");

  if (styleState.karaoke && currentLine.words && currentLine.words.length) {
    let wordHtml = "";
    currentLine.words.forEach((w) => {
      const isSung = t >= w.end;
      const isSinging = t >= w.start && t < w.end;
      const col = isSung || isSinging ? styleState.primary_color : styleState.secondary_color;
      wordHtml += `<span style="color: ${col}; margin: 0 4px;">${escapeHtml(fmt(w.text))}</span>`;
    });
    subText.innerHTML = wordHtml;
  } else {
    subText.textContent = fmt(currentLine.text);
    subText.style.color = styleState.secondary_color;
  }

  // Highlight line in line list
  const activeIdx = timings.indexOf(currentLine);
  document.querySelectorAll(".line-item").forEach((el, idx) => {
    if (idx === activeIdx) {
      el.classList.add("active");
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
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

    // Convert to 1080x1920 reference space
    const refX = Math.round((relX / vidRect.width) * 1080);
    const refY = Math.round((relY / vidRect.height) * 1920);

    styleState.pos_x = refX;
    styleState.pos_y = refY;
    posX.value = refX;
    posY.value = refY;
    posXVal.textContent = `${refX}px`;
    posYVal.textContent = `${refY}px`;

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

  btnPlay.addEventListener("click", () => {
    if (mainVideo.paused) {
      mainVideo.play();
      btnPlay.textContent = "⏸ Pause";
    } else {
      mainVideo.pause();
      btnPlay.textContent = "▶ Play";
    }
  });

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
    renderLineList();
    updateOverlay();
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
      posXVal.textContent = "Center";
      posYVal.textContent = "Grid";
      updateOverlay();
    });
  });

  marginV.addEventListener("input", (e) => {
    styleState.margin_v = parseInt(e.target.value, 10);
    marginVVal.textContent = e.target.value;
    styleState.pos_y = null;
    posYVal.textContent = "Grid";
    updateOverlay();
  });

  posX.addEventListener("input", (e) => {
    styleState.pos_x = parseInt(e.target.value, 10);
    posXVal.textContent = `${e.target.value}px`;
    updateOverlay();
  });

  posY.addEventListener("input", (e) => {
    styleState.pos_y = parseInt(e.target.value, 10);
    posYVal.textContent = `${e.target.value}px`;
    updateOverlay();
  });

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
    updateLofiPreviewCSS();
  });

  const bindAesthetic = (elem, prop) => {
    if (!elem) return;
    elem.addEventListener("change", (e) => {
      aestheticState[prop] = e.target.checked;
      updateLofiPreviewCSS();
    });
  };

  bindAesthetic(fxElCocuy, "el_cocuy_wash");
  bindAesthetic(fxDecimate, "framerate_decimate");
  bindAesthetic(fxIsoNoise, "iso_noise");
  bindAesthetic(fxChromatic, "chromatic_aberration");
  bindAesthetic(fxFoggedGlass, "fogged_glass_vignette");
  bindAesthetic(fxScanlines, "crt_scanlines");
  bindAesthetic(fxSidechain, "sidechain_pump");
  bindAesthetic(fxGlitch, "fuzzed_glitch");

  // Presets load/save
  presetSelect.addEventListener("change", (e) => {
    const p = presets[e.target.value];
    if (p) applyStyle(p);
  });

  btnSavePreset.addEventListener("click", async () => {
    const name = prompt("Name this preset:", "My Custom Style");
    if (!name) return;
    await fetch(`/api/presets/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(styleState),
    });
    await loadPresets();
    presetSelect.value = name;
  });

  // Job Triggers
  btnRealign.addEventListener("click", async () => {
    if (!currentSong) return alert("Select a song first.");
    if (!confirm(`Run Whisper alignment for "${currentSong}"?`)) return;
    startJob("/api/align", { song_name: currentSong }, "Whisper Alignment", () => {
      selectSong(currentSong);
    });
  });

  btnPreview10s.addEventListener("click", () => {
    if (!currentSong) return alert("Select a song first.");
    const curTime = mainVideo.currentTime || 24.0;
    startJob(
      "/api/preview",
      {
        song_name: currentSong,
        style: styleState,
        aesthetics: aestheticState,
        apply_lofi: aestheticState.enabled,
        start_sec: curTime,
        duration_sec: 10.0,
      },
      "10-Second Lo-Fi Preview",
      (job) => {
        if (job.output_url) {
          mainVideo.src = job.output_url;
          mainVideo.play();
          btnPlay.textContent = "⏸ Pause";
        }
      }
    );
  });

  btnRenderFull.addEventListener("click", () => {
    if (!currentSong) return alert("Select a song first.");
    if (!confirm(`Render full Lo-Fi video for "${currentSong}" with audio-reactive effects?`)) return;
    startJob(
      "/api/render",
      {
        song_name: currentSong,
        style: styleState,
        aesthetics: aestheticState,
        lofi_mode: true,
      },
      "Full Lo-Fi Emo-Trap Render",
      () => {
        alert("Render completed! Video saved to sm_content/");
      }
    );
  });

  btnRenderClean.addEventListener("click", () => {
    if (!currentSong) return alert("Select a song first.");
    if (!confirm(`Render clean (fast) video for "${currentSong}"?`)) return;
    startJob(
      "/api/render",
      {
        song_name: currentSong,
        style: styleState,
        lofi_mode: false,
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
  Object.assign(styleState, st);
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
  renderLineList();
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
    jobOutputLinks.innerHTML = "";

    if (activeJobPollInterval) clearInterval(activeJobPollInterval);
    activeJobPollInterval = setInterval(async () => {
      const jRes = await fetch(`/api/jobs/${activeJobId}`);
      if (!jRes.ok) return;
      const job = await jRes.json();

      jobProgressBar.style.width = `${Math.min(100, Math.max(5, job.progress))}%`;
      jobMessage.textContent = job.message;

      if (job.status === "completed") {
        clearInterval(activeJobPollInterval);
        jobMessage.textContent = "Done!";
        if (job.output_url) {
          jobOutputLinks.innerHTML = `<a href="${job.output_url}" target="_blank">Download Video</a>`;
        }
        if (onComplete) onComplete(job);
      } else if (job.status === "failed" || job.status === "cancelled") {
        clearInterval(activeJobPollInterval);
        jobMessage.textContent = job.error || "Job stopped.";
        jobMessage.style.color = "var(--accent-danger)";
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
