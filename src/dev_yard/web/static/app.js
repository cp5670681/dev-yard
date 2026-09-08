(function () {
  const jobId = document.body.getAttribute("data-job");
  if (!jobId) return;
  const logEl = document.querySelector("[data-job-log]");
  const stateEl = document.querySelector("[data-job-state]");
  const formHost = document.querySelector("[data-grill-form]");
  const hintEl = document.querySelector("[data-job-hint]");
  const started = Date.now();
  let formKey = "";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function collectAnswers(form, questions) {
    return questions.map(function (q) {
      const picked = form.querySelector(
        '[name="' + CSS.escape("opt-" + q.id) + '"]:checked'
      );
      const option = picked ? picked.value : q.suggested || "__custom__";
      const box = form.elements["txt-" + q.id];
      const text = box && box.value ? box.value.trim() : "";
      return { id: q.id, option: option, text: text };
    });
  }

  function renderGrill(job) {
    if (!formHost) return;
    const g = job.grill;
    const waiting = job.state === "waiting" && g && g.questions && g.questions.length;
    if (logEl) logEl.classList.toggle("is-folded", Boolean(waiting));
    if (!waiting) {
      formHost.hidden = true;
      if (job.state !== "waiting") formKey = "";
      return;
    }
    const key = job.id + ":" + g.round + ":" + g.questions.map(function (q) { return q.id; }).join(",");
    if (key === formKey) return;
    formKey = key;
    formHost.hidden = false;
    formHost.replaceChildren();
    const form = el("form", "grill-form");
    form.appendChild(el("h2", "", "第 " + g.round + " 轮"));
    if (g.intro) form.appendChild(el("p", "grill-intro", g.intro));
    g.questions.forEach(function (q) {
      const fs = el("fieldset", "grill-q");
      fs.appendChild(el("legend", "", q.id + "  " + (q.title || "")));
      if (q.body) fs.appendChild(el("p", "grill-body", q.body));
      const name = "opt-" + q.id;
      const options = q.options || [];
      const suggested = q.suggested || "";
      options.forEach(function (opt) {
        const lab = el("label", "check");
        const inp = document.createElement("input");
        inp.type = "radio";
        inp.name = name;
        inp.value = opt.id;
        if (opt.id === suggested) inp.checked = true;
        lab.appendChild(inp);
        lab.appendChild(document.createTextNode(" " + opt.id + ". " + opt.label));
        fs.appendChild(lab);
      });
      const customLab = el("label", "check");
      const customInp = document.createElement("input");
      customInp.type = "radio";
      customInp.name = name;
      customInp.value = "__custom__";
      if (!options.length) {
        customInp.checked = true;
        customLab.hidden = true;
      } else if (suggested === "__custom__") {
        customInp.checked = true;
      }
      customLab.appendChild(customInp);
      customLab.appendChild(document.createTextNode(" 自定义"));
      fs.appendChild(customLab);
      const ta = document.createElement("textarea");
      ta.name = "txt-" + q.id;
      ta.rows = 3;
      ta.placeholder = options.length ? "选「自定义」时填写" : "填写或改写建议";
      if (!options.length) ta.value = q.suggested_text || "";
      fs.appendChild(ta);
      form.appendChild(fs);
    });
    const actions = el("div", "grill-actions");
    const submit = el("button", "btn primary", "提交本轮");
    submit.type = "submit";
    const accept = el("button", "btn", "按建议提交");
    accept.type = "button";
    accept.addEventListener("click", function () {
      g.questions.forEach(function (q) {
        if (q.suggested) {
          const inp = form.querySelector(
            '[name="' + CSS.escape("opt-" + q.id) + '"][value="' + CSS.escape(q.suggested) + '"]'
          );
          if (inp) inp.checked = true;
        }
        if (!q.options || !q.options.length) {
          const box = form.elements["txt-" + q.id];
          if (box) box.value = q.suggested_text || "";
        }
      });
      form.requestSubmit();
    });
    actions.appendChild(submit);
    actions.appendChild(accept);
    form.appendChild(actions);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      submit.disabled = true;
      accept.disabled = true;
      const res = await fetch("/api/jobs/" + encodeURIComponent(jobId) + "/answers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers: collectAnswers(form, g.questions) }),
      });
      if (!res.ok) {
        submit.disabled = false;
        accept.disabled = false;
        return;
      }
      formHost.hidden = true;
      formKey = "";
      tick();
    });
    formHost.appendChild(form);
  }

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
    renderGrill(job);
    if (hintEl && job.action === "grill") {
      if (job.state === "waiting") {
        hintEl.textContent = "勾选或改写后提交。有建议的选项已默认选中。";
      } else if (job.state === "running" || job.state === "queued") {
        hintEl.textContent = "正在生成本轮问题…";
      }
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
    if (job.state === "error" || job.state === "waiting") return;
    if (Date.now() - started > 6 * 60 * 60 * 1000) return;
    setTimeout(tick, 1000);
  }
  tick();
})();
