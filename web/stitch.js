// Stitch tab: join the ranges of a long Oto export into one video (stitch_segments.py).
// Self-contained; it only reacts to the "ct:tab" event snippets.js fires when the tab changes.
(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    video: $("stitchVideo"),
    nowPlaying: $("stitchNowPlaying"),
    segments: $("stitchSegments"),
    total: $("stitchTotal"),
    warnings: $("stitchWarnings"),
    folder: $("stitchFolder"),
    btnRefresh: $("btnStitchRefresh"),
    group: $("stitchGroup"),
    groupNote: $("stitchGroupNote"),
    master: $("stitchMaster"),
    audioStart: $("stitchAudioStart"),
    audioAuto: $("stitchAudioAuto"),
    reencode: $("stitchReencode"),
    outJoined: $("stitchOutJoined"),
    outMobile: $("stitchOutMobile"),
    outYoutube: $("stitchOutYoutube"),
    outNote: $("stitchOutNote"),
    btnStitch: $("btnStitch"),
    jobCard: $("stitchJobCard"),
    jobTitle: $("stitchJobTitle"),
    jobBar: $("stitchJobBar"),
    jobMessage: $("stitchJobMessage"),
    btnCancel: $("btnStitchCancel"),
  };
  const GROUP_KEY = "ct.stitch.group";
  const radio = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;

  let data = null; // last /api/stitch-sources response
  let order = []; // [{seg, on}] for the selected group, in join order
  let playingPath = null;
  let jobTimer = null;
  let jobId = null;
  let started = false;

  const group = () => data?.groups.find((g) => g.name === els.group.value) || null;
  const enabled = () => order.filter((o) => o.on).map((o) => o.seg);

  function fmtDur(sec) {
    const t = Math.max(0, Number(sec) || 0);
    const m = Math.floor(t / 60);
    return `${m}:${(t - m * 60).toFixed(2).padStart(5, "0")}`;
  }

  function el(tag, props = {}, ...kids) {
    const node = Object.assign(document.createElement(tag), props);
    kids.forEach((k) => node.append(k));
    return node;
  }

  // ---------------- loading ----------------
  async function load({ keepGroup = true } = {}) {
    const wanted = keepGroup ? els.group.value || savedGroup() : savedGroup();
    els.segments.replaceChildren(el("div", { className: "placeholder-msg", textContent: "Reading the pieces..." }));
    els.btnRefresh.disabled = true;
    try {
      const q = els.folder.value.trim();
      const res = await fetch("/api/stitch-sources" + (q ? `?folder=${encodeURIComponent(q)}` : ""));
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      data = body;
    } catch (err) {
      data = null;
      order = [];
      els.group.replaceChildren(el("option", { value: "", textContent: "No videos found" }));
      els.segments.replaceChildren(
        el("div", {
          className: "placeholder-msg",
          textContent: `${err.message} Put the Oto exports in <project>/oto_generated_clips (or type another folder above).`,
        })
      );
      els.total.textContent = "";
      els.warnings.replaceChildren();
      els.groupNote.textContent = "";
      updateButtons();
      return;
    } finally {
      els.btnRefresh.disabled = false;
    }

    els.folder.value = data.folder;
    els.group.replaceChildren(
      ...(data.groups.length
        ? data.groups.map((g) => el("option", { value: g.name, textContent: `${g.name} (${g.segments.length})` }))
        : [el("option", { value: "", textContent: "No .mp4 pieces in this folder" })])
    );
    if (data.groups.some((g) => g.name === wanted)) els.group.value = wanted;

    els.master.replaceChildren(
      ...(data.masters.length
        ? data.masters.map((m) => el("option", { value: m.path, textContent: `${m.name} (${fmtDur(m.seconds)})` }))
        : [el("option", { value: "", textContent: "No masters in wav_files/" })])
    );
    selectGroup();
  }

  function savedGroup() {
    try { return localStorage.getItem(GROUP_KEY) || ""; } catch (_) { return ""; }
  }

  function selectGroup() {
    const g = group();
    try { if (g) localStorage.setItem(GROUP_KEY, g.name); } catch (_) { /* ignore */ }
    order = g ? g.segments.map((seg) => ({ seg, on: true })) : [];
    // the song's master, when one matches the group name
    if (g?.audio) els.master.value = g.audio.path;
    // Oto fades every export out unless told not to, so the matching master is the safe default
    document.querySelector(`input[name="stitchAudio"][value="${g?.audio ? "master" : "pieces"}"]`).checked = true;
    renderSegments();
    renderSide();
  }

  // ---------------- pieces list ----------------
  function renderSegments() {
    els.segments.replaceChildren();
    if (!order.length) {
      els.segments.append(el("div", { className: "placeholder-msg", textContent: "No pieces." }));
    }
    order.forEach((o, i) => {
      const s = o.seg;
      const check = el("input", { type: "checkbox", checked: o.on, title: "Include this piece" });
      check.addEventListener("change", () => {
        o.on = check.checked;
        renderSegments();
        renderSide();
      });
      const mk = (label, title, fn, disabled = false) => {
        const b = el("button", { className: "btn-tiny", textContent: label, title, disabled });
        b.addEventListener("click", fn);
        return b;
      };
      const move = (d) => () => {
        [order[i], order[i + d]] = [order[i + d], order[i]];
        renderSegments();
        renderSide();
      };
      const row = el(
        "div",
        { className: "stitch-seg" + (o.on ? "" : " off") + (playingPath === s.path ? " playing" : "") },
        check,
        el("span", { className: "idx", textContent: String(i + 1) }),
        el("span", { className: "nm", textContent: s.name, title: s.name }),
        el("span", {
          className: "meta",
          textContent: `${fmtDur(s.seconds)} · ${s.frames}f · ${s.width}×${s.height}@${+s.fps.toFixed(2)} · ${s.size_mb} MB`,
        }),
        el(
          "span",
          { className: "btns" },
          mk("▲", "Move up", move(-1), i === 0),
          mk("▼", "Move down", move(1), i === order.length - 1),
          mk("▶", "Play this piece", () => play(s.url, s.path, s.name))
        )
      );
      els.segments.append(row);
    });
  }

  function play(url, path, label) {
    playingPath = path;
    els.video.src = url;
    els.video.play().catch(() => {});
    els.nowPlaying.textContent = `Playing: ${label}`;
    renderSegments();
  }

  // ---------------- side panel + warnings ----------------
  function renderSide() {
    const g = group();
    const on = enabled();
    const total = on.reduce((t, s) => t + s.seconds, 0);
    els.total.textContent = on.length ? `${on.length} of ${order.length} · ${fmtDur(total)}` : "";

    const warns = [];
    if (g && on.length < 2) warns.push("Select at least two pieces to join.");
    const shapes = new Set(on.map((s) => `${s.width}×${s.height} @ ${+s.fps.toFixed(2)} fps`));
    if (shapes.size > 1) {
      warns.push(`The pieces differ (${[...shapes].join(" / ")}). Tick “Re-encode” or the picture will glitch at the joins.`);
    }
    if (radio("stitchAudio") === "master" && data) {
      const m = data.masters.find((x) => x.path === els.master.value);
      if (!m) warns.push("Pick a song master, or switch to the pieces' own sound.");
      else if (!els.audioAuto.checked && m.seconds - Number(els.audioStart.value || 0) < total - 0.05) {
        warns.push(`The master (${fmtDur(m.seconds)}) is shorter than the picture (${fmtDur(total)}); the sound will end early.`);
      }
    }
    els.warnings.replaceChildren(...warns.map((w) => el("p", { textContent: "⚠ " + w })));

    // group note: resolution, existing result, and a naming hint
    let note = "";
    if (g) {
      note = `${g.segments.length} pieces · ${fmtDur(g.seconds)} · ${g.width}×${g.height} @ ${+g.fps.toFixed(2)} fps`;
      note += g.audio ? ` · master: ${g.audio.name}` : " · no matching master in wav_files/";
      const base = (/^(.*\S) \d+$/.exec(g.name) || [])[1];
      if (base && data.groups.filter((x) => (/^(.*\S) \d+$/.exec(x.name) || [])[1] === base).length > 1) {
        note += `. These look like pieces of one video (“${base} 1”, “${base} 2”...): Oto's naming for one export is “${base}”, “${base}_2”, “${base}_3”.`;
      }
    }
    els.groupNote.textContent = note;

    const sel = g ? g.output : "joined/<name>.mp4";
    els.outJoined.textContent = sel;
    els.outMobile.textContent = (g ? g.compose.mobile.path : "sm_content/<name> - Compose.mp4") + " (Mobile)";
    els.outYoutube.textContent = (g ? g.compose.youtube.path : "sm_content/youtube/<name> - Compose.mp4") + " (YouTube)";
    const bits = [];
    if (g?.output_url) bits.push(`A joined version already exists (${g.output_size_mb} MB). `);
    const target = radio("stitchOut");
    if (g && target !== "joined" && g.compose[target]?.exists) {
      bits.push(`A ${target} Compose video with that name already exists. `);
    }
    bits.push("The file is written aside and only replaces an existing one when the join succeeds.");
    els.outNote.textContent = bits.join("");

    const useMaster = radio("stitchAudio") === "master";
    els.master.disabled = !useMaster || !data?.masters.length;
    els.audioAuto.disabled = !useMaster;
    els.audioStart.disabled = !useMaster || els.audioAuto.checked;
    updateButtons();
  }

  function updateButtons() {
    els.btnStitch.disabled = !group() || enabled().length < 2 || !!jobTimer;
  }

  // ---------------- job ----------------
  async function join() {
    const g = group();
    const files = enabled();
    if (!g || files.length < 2) return;
    const target = radio("stitchOut"); // joined | mobile | youtube
    const compose = target === "joined" ? null : g.compose[target];
    if (compose?.exists && !confirm(`Replace ${compose.path}? The old file is kept until the new one is ready.`)) return;
    const useMaster = radio("stitchAudio") === "master";
    if (useMaster && !els.master.value) return alert("Pick a song master first.");
    const payload = {
      folder: data.folder,
      group: g.name,
      files: files.map((s) => s.name),
      reencode: els.reencode.checked,
      output: compose ? compose.path : g.output,
    };
    if (useMaster) {
      payload.audio = els.master.value;
      payload.audio_start = els.audioAuto.checked ? "auto" : Number(els.audioStart.value) || 0;
    }
    try {
      const res = await fetch("/api/stitch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      jobId = body.job_id;
    } catch (err) {
      return alert(err.message);
    }
    els.jobCard.style.display = "block";
    els.jobTitle.textContent = `Joining ${files.length} pieces`;
    els.jobBar.style.width = "3%";
    els.jobMessage.textContent = "Queued...";
    els.jobMessage.style.color = "";
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
        if (["completed", "failed", "cancelled"].includes(job.status)) {
          finish(job);
        }
      } catch (err) {
        if (++failures >= 5) {
          els.jobMessage.textContent = `Lost contact with the job (${err.message}).`;
          els.jobMessage.style.color = "var(--accent-danger)";
          finish(null);
        }
      }
    }, 600);
    updateButtons();
  }

  function finish(job) {
    clearInterval(jobTimer);
    jobTimer = null;
    if (job?.status === "completed") {
      const warn = /Warning/.test(job.message);
      els.jobMessage.style.color = warn ? "var(--accent-primary)" : "";
      els.jobMessage.replaceChildren(
        document.createTextNode(job.message + " "),
        el("a", { href: job.output_url, download: "", textContent: "Download" })
      );
      play(job.output_url, "result", "joined result");
      load(); // refresh "already exists" info, keep the selection
    } else if (job) {
      els.jobMessage.textContent = job.error || "Stopped.";
      els.jobMessage.style.color = "var(--accent-danger)";
    }
    updateButtons();
  }

  // ---------------- wiring ----------------
  function setup() {
    els.btnRefresh.addEventListener("click", () => load());
    els.folder.addEventListener("keydown", (e) => { if (e.key === "Enter") load({ keepGroup: false }); });
    els.group.addEventListener("change", selectGroup);
    document.querySelectorAll('input[name="stitchAudio"], input[name="stitchOut"]').forEach((r) =>
      r.addEventListener("change", renderSide)
    );
    els.master.addEventListener("change", renderSide);
    els.audioStart.addEventListener("input", renderSide);
    els.audioAuto.addEventListener("change", renderSide);
    els.btnStitch.addEventListener("click", join);
    els.btnCancel.addEventListener("click", () => {
      if (jobId) fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    });
    document.addEventListener("ct:tab", (e) => {
      if (e.detail === "stitch") {
        if (!started) {
          started = true;
          load();
        }
      } else {
        els.video.pause();
      }
    });
    // snippets.js restores the last tab before this listener exists
    let last = "";
    try { last = localStorage.getItem("ct.activeTab") || ""; } catch (_) { /* ignore */ }
    if (last === "stitch" && !started) {
      started = true;
      load();
    }
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
