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
        const tickets = (j.ticket_ids || []).join(",");
        a.appendChild(
          document.createTextNode(
            j.jira + " · " + j.action + (tickets ? " " + tickets : "")
          )
        );
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

  const BOARD_COLS = [
    "pending",
    "ready",
    "implementing",
    "implemented",
    "reviewing",
    "blocked",
    "done",
  ];

  function ticketForm(jira, action, tid, label, enabled) {
    const form = document.createElement("form");
    form.method = "post";
    form.action = "/r/" + encodeURIComponent(jira) + "/actions/" + action;
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "ticket_id";
    hidden.value = tid;
    form.appendChild(hidden);
    const btn = el("button", "btn tiny", label);
    btn.type = "submit";
    if (!enabled) btn.disabled = true;
    form.appendChild(btn);
    return form;
  }

  function ticketCard(jira, t) {
    const art = el("article", "ticket");
    art.setAttribute("data-ticket", t.id);
    art.appendChild(
      el("div", "ticket-id", t.id + (t.parallel ? " · parallel" : ""))
    );
    art.appendChild(el("h3", "", t.title || t.id));
    const deps = t.depends_on && t.depends_on.length ? t.depends_on.join(", ") : "";
    art.appendChild(
      el("p", "meta", (t.repo || "") + (deps ? " · 依赖 " + deps : ""))
    );
    if (t.last_summary) {
      art.appendChild(el("p", "summary", String(t.last_summary).slice(0, 240)));
    }
    const actions = el("div", "ticket-actions");
    actions.appendChild(ticketForm(jira, "implement", t.id, "实现", t.can_implement));
    actions.appendChild(ticketForm(jira, "review", t.id, "审查", t.can_review));
    art.appendChild(actions);
    return art;
  }

  function renderBoard(detail) {
    const board = document.querySelector("[data-board]");
    if (!board || !detail || !detail.tickets) return;
    const jira = board.getAttribute("data-jira");
    const byState = {};
    BOARD_COLS.forEach(function (c) {
      byState[c] = [];
    });
    detail.tickets.forEach(function (t) {
      const state = t.state || "pending";
      if (!byState[state]) byState[state] = [];
      byState[state].push(t);
    });
    board.replaceChildren();
    BOARD_COLS.forEach(function (col) {
      const wrap = el("div", "col");
      wrap.setAttribute("data-col", col);
      const header = document.createElement("header");
      header.appendChild(el("span", "dot state-" + col));
      header.appendChild(document.createTextNode(" " + col + " "));
      const em = document.createElement("em");
      em.textContent = String((byState[col] || []).length);
      header.appendChild(em);
      wrap.appendChild(header);
      const tickets = byState[col] || [];
      if (!tickets.length) {
        wrap.appendChild(el("p", "col-empty", "—"));
      } else {
        tickets.forEach(function (t) {
          wrap.appendChild(ticketCard(jira, t));
        });
      }
      board.appendChild(wrap);
    });
  }

  function refreshBoard() {
    const board = document.querySelector("[data-board]");
    if (!board) return;
    const jira = board.getAttribute("data-jira");
    if (!jira) return;
    fetch("/api/requirements/" + encodeURIComponent(jira))
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (detail) {
        if (detail) renderBoard(detail);
      })
      .catch(function () {});
  }

  const navEs = new EventSource("/api/jobs/events");
  navEs.addEventListener("jobs", function (e) {
    renderNavJobs(JSON.parse(e.data));
    refreshBoard();
  });

  function bindJob(jobId, root) {
    const logEl = root.querySelector("[data-job-log]");
    const stateEl = root.querySelector("[data-job-state]");
    const formHost = root.querySelector("[data-grill-form]");
    const hintEl = root.querySelector("[data-job-hint]");
    const pctEl = root.querySelector("[data-job-pct]");
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
      refreshBoard();
      if (job.state !== "ok") return;
      fetch("/api/jobs")
        .then(function (res) {
          return res.ok ? res.json() : [];
        })
        .then(function (items) {
          const others = (items || []).filter(function (row) {
            return row.jira === job.jira && row.id !== job.id;
          });
          if (!others.length) goOk();
        })
        .catch(function () {
          goOk();
        });
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
  }

  const panels = document.querySelectorAll("[data-job-panel][data-job-id]");
  if (panels.length) {
    panels.forEach(function (panel) {
      bindJob(panel.getAttribute("data-job-id"), panel);
    });
    return;
  }
  const jobId = document.body.getAttribute("data-job");
  if (jobId) bindJob(jobId, document);
})();
