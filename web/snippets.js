// Snippets tab: cut a rendered "<song> - lyrics.mp4", close with a transition + the end card.
// Self-contained; it only shares the preview modal (#previewPanel / #previewVideo) with app.js.
(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    layoutStudio: document.querySelector("main.studio-layout:not(.snippet-layout)"),
    layoutSnip: $("snipLayout") || $("snippetLayout"),
    songBar: $("studioSongBar"),
    video: $("snipVideo"),
    frame: $("snipFrame"),
    strip: $("snipStrip"),
    stripSel: $("snipStripSel"),
    stripHead: $("snipStripHead"),
    start: $("snipStart"),
    end: $("snipEnd"),
    loop: $("snipLoop"),
    length: $("snipLength"),
    song: $("snipSong"),
    songNote: $("snipSongNote"),
    transitions: $("snipTransitions"),
    transDur: $("snipTransDur"),
    transDurVal: $("snipTransDurVal"),
    text: $("snipText"),
    cardDur: $("snipCardDur"),
    cardDurVal: $("snipCardDurVal"),
    music: $("snipMusic"),
    fadeIn: $("snipFadeIn"),
    btnPlaySel: $("btnSnipPlaySel"),
    btnCard: $("btnSnipCard"),
    btnPreview: $("btnSnipPreview"),
    btnExport: $("btnSnipExport"),
    jobCard: $("snipJobCard"),
    jobTitle: $("snipJobTitle"),
    jobBar: $("snipJobBar"),
    jobMessage: $("snipJobMessage"),
    btnCancel: $("btnSnipCancel"),
    list: $("snipList"),
  };
  const NUDGE = 0.1;
  const TAB_KEY = "ct.activeTab";
  const settingsKey = (song) => `ct.snippet.${song}`;

  let sources = [];
  let defaults = null;
  let transitions = new Set();
  let playingSelection = false;
  let jobTimer = null;
  let jobId = null;
  let frameUrl = null;
  let loaded = false;

  // ---------------- tabs ----------------
  function showTab(name) {
    const snip = name === "snippets";
    els.layoutStudio.hidden = snip;
    els.layoutSnip.hidden = !snip;
    if (els.songBar) els.songBar.hidden = snip;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    try { localStorage.setItem(TAB_KEY, name); } catch (_) { /* ignore */ }
    if (snip) {
      const main = document.getElementById("mainVideo");
      if (main && !main.paused) main.pause();
      if (!loaded) init();
    } else {
      els.video.pause();
    }
  }

  // ---------------- state <-> controls ----------------
  const duration = () => (Number.isFinite(els.video.duration) ? els.video.duration : currentSource()?.duration || 0);
  const currentSource = () => sources.find((s) => s.name === els.song.value);
  const num = (el) => Number(el.value);

  function config() {
    return {
      start: num(els.start),
      end: num(els.end),
      transitions: [...transitions],
      transition_duration: num(els.transDur),
      card_duration: num(els.cardDur),
      release_text: els.text.value,
      music_under_card: els.music.checked,
      fade_in: els.fadeIn.checked,
    };
  }

  function applyConfig(cfg) {
    const c = { ...defaults, ...cfg };
    els.start.value = Number(c.start).toFixed(2);
    els.end.value = Number(c.end).toFixed(2);
    transitions = new Set(c.transitions || []);
    els.transDur.value = c.transition_duration;
    els.cardDur.value = c.card_duration;
    els.text.value = c.release_text;
    els.music.checked = !!c.music_under_card;
    els.fadeIn.checked = !!c.fade_in;
    renderChips();
    refresh();
  }

  function saveSettings() {
    if (!els.song.value) return;
    try { localStorage.setItem(settingsKey(els.song.value), JSON.stringify(config())); } catch (_) { /* ignore */ }
  }

  function setTime(which, value) {
    const d = duration() || Infinity;
    let v = Math.max(0, Math.min(d, Number(value) || 0));
    if (which === "start") {
      els.start.value = v.toFixed(2);
      if (num(els.end) <= v) els.end.value = Math.min(d, v + 5).toFixed(2);
    } else {
      els.end.value = v.toFixed(2);
      if (num(els.start) >= v) els.start.value = Math.max(0, v - 5).toFixed(2);
    }
    hideFrame();
    refresh();
  }

  function refresh() {
    const s = num(els.start);
    const e = num(els.end);
    els.transDurVal.textContent = `${num(els.transDur).toFixed(2)} s`;
    els.cardDurVal.textContent = `${num(els.cardDur).toFixed(1)} s`;
    els.length.textContent = `${Math.max(0, e - s).toFixed(2)} s + ${num(els.cardDur).toFixed(1)} s card`;
    const d = duration();
    if (d > 0) {
      els.stripSel.style.left = `${(s / d) * 100}%`;
      els.stripSel.style.width = `${(Math.max(0, e - s) / d) * 100}%`;
      els.stripHead.style.left = `${(els.video.currentTime / d) * 100}%`;
    }
    saveSettings();
  }

  function renderChips() {
    els.transitions.innerHTML = "";
    (window.__snipTransitionList || []).forEach((t) => {
      const chip = document.createElement("button");
      chip.className = "chip" + (transitions.has(t.id) ? " on" : "");
      chip.textContent = t.label;
      chip.dataset.id = t.id;
      chip.addEventListener("click", () => {
        if (transitions.has(t.id)) transitions.delete(t.id);
        else transitions.add(t.id);
        chip.classList.toggle("on", transitions.has(t.id));
        hideFrame();
        saveSettings();
      });
      els.transitions.appendChild(chip);
    });
  }

  // ---------------- loading ----------------
  async function init() {
    loaded = true;
    try {
      const res = await fetch("/api/snippet-sources");
      const data = await res.json();
      sources = data.songs || [];
      defaults = data.defaults;
      window.__snipTransitionList = data.transitions || [];
      els.song.innerHTML = "";
      if (!sources.length) {
        els.song.innerHTML = '<option value="">No rendered videos yet</option>';
        els.songNote.textContent = 'Render a song in the Studio tab first: snippets are cut from "<song> - lyrics.mp4".';
      }
      sources.forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s.name;
        opt.textContent = s.name;
        els.song.appendChild(opt);
      });
      if (!data.logo) els.songNote.textContent += " Logo file not found: the end card will only show the text.";
      if (sources.length) selectSong(sources[0].name);
      else applyConfig({});
    } catch (err) {
      els.song.innerHTML = `<option value="">Failed to load: ${err.message}</option>`;
    }
    loadList();
  }

  function selectSong(name) {
    els.song.value = name;
    hideFrame();
    els.video.src = `/api/video/snippet-source/${encodeURIComponent(name)}`;
    els.video.load();
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(settingsKey(name)) || "{}"); } catch (_) { saved = {}; }
    applyConfig(saved);
  }

  async function loadList() {
    try {
      const res = await fetch("/api/snippets");
      const items = (await res.json()).snippets || [];
      els.list.innerHTML = items.length ? "" : '<div class="placeholder-msg">None yet.</div>';
      items.forEach((it) => {
        const row = document.createElement("div");
        row.className = "snip-item";
        row.innerHTML = '<span></span><span><a href="#" data-play>play</a><a download>download</a></span>';
        row.querySelector("span").textContent = `${it.name} (${it.size_mb} MB)`;
        row.querySelector("span").title = it.name;
        row.querySelector("[download]").href = it.url;
        row.querySelector("[data-play]").addEventListener("click", (e) => {
          e.preventDefault();
          openModal(it.url);
        });
        els.list.appendChild(row);
      });
    } catch (_) { /* ignore */ }
  }

  function openModal(url) {
    const panel = document.getElementById("previewPanel");
    const pv = document.getElementById("previewVideo");
    els.video.pause();
    pv.src = url;
    panel.hidden = false;
    pv.play().catch(() => {});
  }

  // ---------------- preview frame ----------------
  function hideFrame() {
    els.frame.hidden = true;
  }

  async function previewCard() {
    if (!els.song.value) return;
    const cfg = config();
    els.btnCard.disabled = true;
    try {
      const res = await fetch("/api/snippet-frame", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          song_name: els.song.value, config: cfg,
          time_sec: cfg.end - cfg.start + cfg.card_duration * 0.6,
        }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      if (frameUrl) URL.revokeObjectURL(frameUrl);
      frameUrl = URL.createObjectURL(await res.blob());
      els.video.pause();
      els.frame.src = frameUrl;
      els.frame.hidden = false;
    } catch (err) {
      alert(`End card preview failed: ${err.message}`);
    } finally {
      els.btnCard.disabled = false;
    }
  }

  // ---------------- jobs ----------------
  function setBusy(busy) {
    [els.btnPreview, els.btnExport].forEach((b) => (b.disabled = busy));
  }

  async function startJob(url, title, onDone) {
    if (!els.song.value) return alert("Pick a video first.");
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ song_name: els.song.value, config: config() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      jobId = data.job_id;
    } catch (err) {
      alert(err.message);
      return;
    }
    els.jobCard.style.display = "block";
    els.jobTitle.textContent = title;
    els.jobBar.style.width = "3%";
    els.jobMessage.textContent = "Queued...";
    els.jobMessage.style.color = "";
    setBusy(true);
    clearInterval(jobTimer);
    const mine = jobId;
    let failures = 0;
    jobTimer = setInterval(async () => {
      try {
        const r = await fetch(`/api/jobs/${mine}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        failures = 0;
        const job = await r.json();
        els.jobBar.style.width = `${Math.max(3, Math.min(100, job.progress))}%`;
        els.jobMessage.textContent = job.message;
        if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
          clearInterval(jobTimer);
          setBusy(false);
          if (job.status === "completed") onDone(job);
          else {
            els.jobMessage.textContent = job.error || "Stopped.";
            els.jobMessage.style.color = "var(--accent-danger)";
          }
        }
      } catch (err) {
        if (++failures >= 5) {
          clearInterval(jobTimer);
          setBusy(false);
          els.jobMessage.textContent = `Lost contact with the job (${err.message}).`;
          els.jobMessage.style.color = "var(--accent-danger)";
        }
      }
    }, 600);
  }

  // ---------------- wiring ----------------
  function setup() {
    document.querySelectorAll(".tab-btn").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

    els.song.addEventListener("change", () => selectSong(els.song.value));
    els.video.addEventListener("loadedmetadata", refresh);
    els.video.addEventListener("timeupdate", () => {
      refresh();
      if (playingSelection && els.video.currentTime >= num(els.end)) {
        if (els.loop.checked) els.video.currentTime = num(els.start);
        else {
          els.video.pause();
          playingSelection = false;
        }
      }
    });
    els.video.addEventListener("play", hideFrame);
    els.video.addEventListener("seeking", hideFrame);
    els.frame.addEventListener("click", hideFrame);

    els.strip.addEventListener("click", (e) => {
      const d = duration();
      if (!d) return;
      const r = els.strip.getBoundingClientRect();
      els.video.currentTime = ((e.clientX - r.left) / r.width) * d;
    });

    els.start.addEventListener("change", () => setTime("start", els.start.value));
    els.end.addEventListener("change", () => setTime("end", els.end.value));
    document.querySelectorAll("[data-snip]").forEach((b) =>
      b.addEventListener("click", () => {
        const now = els.video.currentTime || 0;
        switch (b.dataset.snip) {
          case "start-": setTime("start", num(els.start) - NUDGE); break;
          case "start+": setTime("start", num(els.start) + NUDGE); break;
          case "end-": setTime("end", num(els.end) - NUDGE); break;
          case "end+": setTime("end", num(els.end) + NUDGE); break;
          case "start-now": setTime("start", now); break;
          case "end-now": setTime("end", now); break;
        }
        // show the frame at the edited edge
        if (b.dataset.snip.startsWith("start")) els.video.currentTime = num(els.start);
        else els.video.currentTime = num(els.end);
      })
    );

    els.btnPlaySel.addEventListener("click", () => {
      els.video.currentTime = num(els.start);
      playingSelection = true;
      els.video.play().catch(() => {});
    });
    els.video.addEventListener("pause", () => { playingSelection = playingSelection && !els.video.ended; });

    [els.transDur, els.cardDur].forEach((el) => el.addEventListener("input", () => { hideFrame(); refresh(); }));
    [els.text, els.music, els.fadeIn].forEach((el) => el.addEventListener("change", () => { hideFrame(); refresh(); }));

    els.btnCard.addEventListener("click", previewCard);
    els.btnPreview.addEventListener("click", () =>
      startJob("/api/snippet-preview", "Transition preview", (job) => {
        els.jobMessage.textContent = "Done.";
        openModal(job.output_url);
      })
    );
    els.btnExport.addEventListener("click", () =>
      startJob("/api/snippet", "Export snippet", (job) => {
        els.jobMessage.innerHTML = `Done. <a href="${job.output_url}" download>Download</a>`;
        loadList();
      })
    );
    els.btnCancel.addEventListener("click", () => {
      if (jobId) fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    });

    let initial = "studio";
    try { initial = localStorage.getItem(TAB_KEY) || "studio"; } catch (_) { /* ignore */ }
    showTab(initial);
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
