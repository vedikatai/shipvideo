(() => {
  const form = document.getElementById("demo-form");
  const urlInput = document.getElementById("url");
  const stepsSelect = document.getElementById("max-steps");
  const submitBtn = document.getElementById("submit");
  const statusEl = document.getElementById("status");
  const progressBar = document.getElementById("progress-bar");
  const timeline = document.getElementById("timeline");
  const videoEl = document.getElementById("video");
  const placeholder = document.getElementById("placeholder");
  const errEl = document.getElementById("err");
  const actions = document.getElementById("actions");
  const stageLabel = document.getElementById("stage-label");

  let pollTimer = null;

  function setStatus(status) {
    statusEl.className = "status-pill " + (status || "queued");
    statusEl.textContent = status || "idle";
  }

  function showError(msg) {
    if (!msg) {
      errEl.classList.remove("show");
      errEl.textContent = "";
      return;
    }
    errEl.textContent = msg;
    errEl.classList.add("show");
  }

  function setProgress(pct) {
    progressBar.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }

  function renderTimeline(steps) {
    timeline.innerHTML = "";
    if (!steps || !steps.length) {
      timeline.innerHTML = '<li><div class="n">…</div><div><div class="t">Waiting for capture…</div><div class="d">Screenshots and narration appear here.</div></div></li>';
      return;
    }
    steps.forEach((s, i) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <div class="n">${i + 1}</div>
        <div>
          <div class="t">${escapeHtml(s.title || s.url || "Step")}</div>
          <div class="d">${escapeHtml(s.subtitle || s.label || s.action || "")}</div>
        </div>`;
      timeline.appendChild(li);
    });
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function setActions(jobId, ready) {
    actions.innerHTML = "";
    if (!ready) return;
    const v = document.createElement("a");
    v.href = `/api/jobs/${jobId}/video`;
    v.download = `shipvideo-${jobId}.mp4`;
    v.textContent = "Download MP4";
    const s = document.createElement("a");
    s.href = `/api/jobs/${jobId}/srt`;
    s.download = `shipvideo-${jobId}.srt`;
    s.textContent = "Download SRT";
    actions.appendChild(v);
    actions.appendChild(s);
  }

  async function pollJob(jobId) {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error("Failed to load job");
      const job = await res.json();
      setStatus(job.status);
      stageLabel.textContent = job.stage || job.status || "";

      const stages = ["queued", "starting", "capture_start", "capture_done", "render_start", "render_done", "done"];
      const idx = Math.max(0, stages.indexOf(job.stage));
      setProgress(job.status === "done" ? 100 : Math.round(((idx + 1) / stages.length) * 100));

      const steps = (job.result && job.result.steps) || [];
      if (steps.length) renderTimeline(steps);

      if (job.status === "done") {
        clearInterval(pollTimer);
        pollTimer = null;
        submitBtn.disabled = false;
        placeholder.style.display = "none";
        videoEl.style.display = "block";
        videoEl.src = `/api/jobs/${jobId}/video?t=${Date.now()}`;
        videoEl.load();
        setActions(jobId, true);
        showError(null);
        return;
      }
      if (job.status === "failed") {
        clearInterval(pollTimer);
        pollTimer = null;
        submitBtn.disabled = false;
        showError(job.error || "Job failed");
        setProgress(100);
      }
    } catch (e) {
      showError(e.message || String(e));
    }
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showError(null);
    setActions(null, false);
    videoEl.removeAttribute("src");
    videoEl.style.display = "none";
    placeholder.style.display = "block";
    renderTimeline([]);
    setProgress(5);
    setStatus("queued");
    stageLabel.textContent = "queued";
    submitBtn.disabled = true;

    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }

    const url = urlInput.value.trim();
    const max_steps = parseInt(stepsSelect.value, 10) || 10;

    try {
      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, max_steps }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || "Could not start job");
      }
      const job = await res.json();
      setStatus(job.status);
      pollTimer = setInterval(() => pollJob(job.id), 1200);
      pollJob(job.id);
    } catch (e) {
      submitBtn.disabled = false;
      showError(e.message || String(e));
      setStatus("failed");
    }
  });

  renderTimeline([]);
})();
