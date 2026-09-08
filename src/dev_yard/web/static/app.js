(function () {
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderNavJobs(items) {
    const host = document.querySelector("[data-nav-jobs]");
    const list = host && host.querySelector("[data-nav-job-list]");
    if (!host || !list) return;
    if (!items.length) {
      host.hidden = true;
      list.replaceChildren();
      return;
    }
    host.hidden = false;
    const frag = document.createDocumentFragment();
    items.forEach(function (j) {
      const waiting = j.state === "waiting";
      const a = el("a", waiting ? "is-waiting" : "");
      if (j.action === "repo_add") {
        a.href = "/repos?job=" + encodeURIComponent(j.id);
        a.textContent = "仓库 · clone";
      } else {
        a.href = "/r/" + encodeURIComponent(j.jira) + "?job=" + encodeURIComponent(j.id);
        a.appendChild(document.createTextNode(j.jira + " · " + j.action));
        if (waiting) {
          const dot = el("span", "nav-dot");
          dot.setAttribute("aria-label", "需要确认");
          a.appendChild(dot);
        }
      }
      frag.appendChild(a);
    });
    list.replaceChildren(frag);
  }

  const navEs = new EventSource("/api/jobs/events");
  navEs.addEventListener("jobs", function (e) {
    renderNavJobs(JSON.parse(e.data));
  });

  const jobId = document.body.getAttribute("data-job");
  if (!jobId) return;
  const logEl = document.querySelector("[data-job-log]");
  const stateEl = document.querySelector("[data-job-state]");
  const formHost = document.querySelector("[data-grill-form]");
  const hintEl = document.querySelector("[data-job-hint]");
  const started = Date.now();
  let formKey = "";

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
    });
    formHost.appendChild(form);
  }

  function updatePct(text) {
    const pctEl = document.querySelector("[data-job-pct]");
    if (!pctEl) return;
    const pcts = [...(text || "").matchAll(/(\d+)\s*%/g)];
    if (pcts.length) pctEl.style.width = pcts[pcts.length - 1][1] + "%";
  }

  function applyLog(text, replace) {
    if (!logEl) return;
    if (replace) {
      logEl.textContent = text || "";
      updatePct(text || "");
    } else if (text) {
      logEl.textContent += text;
      updatePct(text);
    }
    logEl.scrollTop = logEl.scrollHeight;
  }

  function applyState(job) {
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
  }

  function goOk() {
    const url = new URL(window.location.href);
    url.searchParams.delete("job");
    url.searchParams.set("ok", "1");
    const next = url.pathname + "?" + url.searchParams.toString();
    const here = window.location.pathname + window.location.search;
    if (here !== next) window.location.replace(next);
  }

  function finish(job) {
    es.close();
    applyState(job);
    if (job.log != null) applyLog(job.log, true);
    if (job.state === "ok") goOk();
  }

  const es = new EventSource("/api/jobs/" + encodeURIComponent(jobId) + "/events");
  es.addEventListener("snapshot", function (e) {
    const job = JSON.parse(e.data);
    applyLog(job.log || "", true);
    applyState(job);
    if (job.state === "ok" || job.state === "error") finish(job);
  });
  es.addEventListener("log", function (e) {
    applyLog(JSON.parse(e.data), false);
  });
  es.addEventListener("state", function (e) {
    applyState(JSON.parse(e.data));
  });
  es.addEventListener("done", function (e) {
    finish(JSON.parse(e.data));
  });
  es.onerror = function () {
    if (Date.now() - started > 6 * 60 * 60 * 1000) es.close();
  };
})();
