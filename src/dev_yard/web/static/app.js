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

    function renderPiRuns(job) {
      const host = root.querySelector("[data-pi-runs]");
      if (!host || !job.pi_runs) return;
      const runs = job.pi_runs;
      host.hidden = !runs.length;
      host.replaceChildren();
      runs.forEach(function (run, i) {
        const a = el(
          "a",
          "",
          runs.length > 1 ? "查看对话 · " + (i + 1) : "查看对话"
        );
        a.href = "#";
        a.setAttribute("data-open-pi", "");
        a.setAttribute("data-job", job.id);
        a.setAttribute("data-run", String(run.index != null ? run.index : i));
        host.appendChild(a);
      });
    }

    function applyState(job) {
      if (stateEl) {
        stateEl.textContent = job.state;
        stateEl.className = "pill job-" + job.state;
      }
      renderPiRuns(job);
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

  let piEs = null;
  let piFollow = true;

  function closePiDrawer() {
    const host = document.querySelector("[data-pi-drawer]");
    if (piEs) {
      piEs.close();
      piEs = null;
    }
    if (host) host.hidden = true;
  }

  function ensurePiDrawer() {
    let host = document.querySelector("[data-pi-drawer]");
    if (host) return host;
    host = el("div", "pi-drawer-root");
    host.setAttribute("data-pi-drawer", "");
    host.hidden = true;
    const backdrop = el("div", "pi-backdrop");
    backdrop.setAttribute("data-pi-close", "");
    const drawer = el("aside", "pi-drawer");
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-label", "pi 对话");
    const head = el("header", "pi-drawer-head");
    const titles = el("div");
    const title = el("strong", "", "pi 对话");
    title.setAttribute("data-pi-title", "");
    const meta = el("p", "meta");
    meta.setAttribute("data-pi-meta", "");
    titles.appendChild(title);
    titles.appendChild(meta);
    const closeBtn = el("button", "btn tiny", "关闭");
    closeBtn.type = "button";
    closeBtn.setAttribute("data-pi-close", "");
    head.appendChild(titles);
    head.appendChild(closeBtn);
    const chat = el("div", "pi-chat");
    chat.setAttribute("data-pi-chat", "");
    drawer.appendChild(head);
    drawer.appendChild(chat);
    host.appendChild(backdrop);
    host.appendChild(drawer);
    document.body.appendChild(host);
    chat.addEventListener("scroll", function () {
      const gap = chat.scrollHeight - chat.scrollTop - chat.clientHeight;
      piFollow = gap < 48;
    });
    return host;
  }

  function appendPiEntry(chat, entry) {
    const art = el("article", "pi-msg role-" + (entry.role || "assistant"));
    const labels = { user: "user", assistant: "assistant", toolResult: "tool" };
    art.appendChild(el("div", "pi-role", labels[entry.role] || entry.role || ""));
    if (entry.thinking) {
      const d = document.createElement("details");
      d.className = "pi-fold";
      d.appendChild(el("summary", "", "思考"));
      d.appendChild(el("pre", "", entry.thinking));
      art.appendChild(d);
    }
    if (entry.role === "toolResult") {
      const d = document.createElement("details");
      d.className = "pi-fold";
      const mark = entry.is_error ? "error · " : "";
      d.appendChild(el("summary", "", mark + (entry.tool_name || "result")));
      if (entry.text) d.appendChild(el("pre", "", entry.text));
      art.appendChild(d);
    } else if (entry.text) {
      art.appendChild(el("div", "pi-text", entry.text));
    }
    (entry.tools || []).forEach(function (t) {
      const d = document.createElement("details");
      d.className = "pi-fold";
      d.appendChild(el("summary", "", t.name || "tool"));
      if (t.args) d.appendChild(el("pre", "", t.args));
      art.appendChild(d);
    });
    chat.appendChild(art);
    if (piFollow) chat.scrollTop = chat.scrollHeight;
  }

  function openPiDrawer(jobId, run) {
    const host = ensurePiDrawer();
    const chat = host.querySelector("[data-pi-chat]");
    const meta = host.querySelector("[data-pi-meta]");
    const title = host.querySelector("[data-pi-title]");
    chat.replaceChildren();
    chat.appendChild(el("p", "pi-empty", "连接对话流…"));
    piFollow = true;
    host.hidden = false;
    if (piEs) {
      piEs.close();
      piEs = null;
    }
    const url =
      "/api/jobs/" + encodeURIComponent(jobId) + "/pi/" + encodeURIComponent(run) + "/events";
    piEs = new EventSource(url);
    piEs.addEventListener("snapshot", function (e) {
      const data = JSON.parse(e.data);
      title.textContent = "pi 对话";
      meta.textContent =
        (data.cwd || "") + (data.found ? "" : " · 等待 session…");
      const empty = chat.querySelector(".pi-empty");
      if (empty && data.found) empty.remove();
    });
    piEs.addEventListener("entry", function (e) {
      const empty = chat.querySelector(".pi-empty");
      if (empty) empty.remove();
      appendPiEntry(chat, JSON.parse(e.data));
    });
    piEs.addEventListener("done", function () {
      if (piEs) {
        piEs.close();
        piEs = null;
      }
      if (!chat.querySelector(".pi-msg") && !chat.querySelector(".pi-empty")) {
        chat.appendChild(el("p", "pi-empty", "没有找到对话记录"));
      }
    });
  }

  document.addEventListener("click", function (e) {
    const closer = e.target.closest("[data-pi-close]");
    if (closer) {
      closePiDrawer();
      return;
    }
    const a = e.target.closest("[data-open-pi]");
    if (!a) return;
    e.preventDefault();
    openPiDrawer(a.getAttribute("data-job"), a.getAttribute("data-run") || "0");
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closePiDrawer();
  });

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
