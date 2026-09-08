(function () {
  const jobId = document.body.getAttribute("data-job");
  if (!jobId) return;
  const logEl = document.querySelector("[data-job-log]");
  const stateEl = document.querySelector("[data-job-state]");
  const started = Date.now();

  async function tick() {
    const res = await fetch("/api/jobs/" + encodeURIComponent(jobId));
    if (!res.ok) return;
    const job = await res.json();
    if (logEl) logEl.textContent = job.log || "";
    const pctEl = document.querySelector("[data-job-pct]");
    const pcts = [...(job.log || "").matchAll(/(\d+)\s*%/g)];
    if (pctEl && pcts.length) {
      pctEl.style.width = pcts[pcts.length - 1][1] + "%";
    }
    if (stateEl) {
      stateEl.textContent = job.state;
      stateEl.className = "pill job-" + job.state;
    }
    if (logEl) logEl.scrollTop = logEl.scrollHeight;
    if (job.state === "ok") {
      const url = new URL(window.location.href);
      url.searchParams.delete("job");
      url.searchParams.set("ok", "1");
      const next = url.pathname + "?" + url.searchParams.toString();
      const here = window.location.pathname + window.location.search;
      if (here !== next) window.location.replace(next);
      return;
    }
    if (job.state === "error") return;
    if (Date.now() - started > 6 * 60 * 60 * 1000) return;
    setTimeout(tick, 1000);
  }
  tick();
})();
