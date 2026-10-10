"""Requirement test entry.

Case documents live in ``qa_cases``, design in ``qa_design``, and one run's
lock, resume, and verdicts in ``qa_runloop``. ``req_test`` routes a single
invocation across those three.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from contextlib import nullcontext as _nullcontext
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from dev_yard import attachments, paths, qa_design_refs, qa_run
from dev_yard import qa_state as qa_st
from dev_yard.config import load_repos
from dev_yard.qa_cases import (
    _involved_aliases,
    discover_cases,
    split_frontmatter,
    uncovered_changes,
)
from dev_yard.qa_config import TestRejected, load_qa_config
from dev_yard.qa_design import (
    _DESIGN_PENDING,
    _count_open_questions,
    _design_prompt,
    _duties,
    _finish_design,
    _is_account_gap,
    _mark_design_blocked,
    _mark_design_pending,
    _permission_gap_warning,
    _raise_if_cancelled,
    _verify_loop,
    design_pending,
    lint_cases,
    open_questions_payload,
    read_design_marker,
    repair_keeps_assertions,
    write_context_md,
)
from dev_yard.qa_exec import (
    case_needs_exec_site,
    diagnose_pi_exit,
    exec_site_for_job,
    host_case_verdict,
    ping_case_sites,
    recheck_db_assertions,
    run_case_script,
)
from dev_yard.qa_report import has_design_blocked_skip, map_qa_result, triage_buckets
from dev_yard.qa_review import (
    approve_cases,
    cases_fingerprint,
    clear_stale,
    mark_machine_fixed,
    reject_cases,
    review_gate,
    review_payload,
)
from dev_yard.qa_runloop import (
    _apply_resume,
    _auth_failure_for,
    _case_account,
    _case_auth_env,
    _check_case_accounts,
    _check_case_repos,
    _claim_run_dir,
    _gate,
    _head_sha,
    _hold_unselected,
    _latest_retryable_run,
    _pending_in,
    _porcelain,
    _preload_auth,
    _progress_doc,
    _read_case_result,
    _real_pool_probe,
    _reject_selective,
    _release_held_cases,
    _root_png_names,
    _run_incomplete,
    _run_lock,
    _run_prompt,
    _summarize,
    _tree_state,
    _used_accounts,
    _write_progress,
    _write_skipped_result,
    affected_covers,
    find_incomplete_run,
    find_run_for_rerun,
    incomplete_run_payload,
    latest_retryable,
    load_yaml_tolerant,
    persist_case_verdict,
    reconcile_case_verdict,
    reconcile_progress_doc,
    reset_cases_in_run,
)
from dev_yard.qa_schedule import (
    TERMINAL,
    CaseJob,
    PoolSlot,
    normalize_status,
    now_iso,
    run_schedule,
)

# Re-exported for callers that still import the pre-split surface.
__all__ = [
    "_duties",
    "_pending_in",
    "_progress_doc",
    "_run_incomplete",
    "incomplete_run_payload",
    "load_yaml_tolerant",
    "reconcile_case_verdict",
    "reconcile_progress_doc",
    "repair_keeps_assertions",
    "split_frontmatter",
]
from dev_yard.qa_verify import (
    VerifyResult,
    describe,
    env_lock,
    prior_defects,
    render_prior_defects,
    verify_cases,
    verify_gate,
    verify_view,
    write_blocked,
    write_summary,
)
from dev_yard.runners import (
    Runner,
    get_runner,
    pi_argv,
    run_pi_print_tracked,
)
from dev_yard.skillbind import session_prompt_for
from dev_yard.stages import load_registry
from dev_yard.test_report import ReportRejected, accept_test_report

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]
SpawnFn = Callable[[subprocess.Popen[str]], None]
ReapFn = Callable[[subprocess.Popen[str]], None]


def req_test(
    root: Path,
    jira: str,
    *,
    env: str | None = None,
    print_mode: bool = False,
    design_only: bool = False,
    run_only: bool = False,
    redesign: bool = False,
    approve: bool = False,
    feedback: str | None = None,
    design_notes: str | None = None,
    design_refs: list[str] | str | None = None,
    ingest: bool = True,
    resume: bool | None = None,
    rerun_cases: list[str] | None = None,
    verify: bool | None = None,
    verify_only: bool = False,
    allow_unverified: bool = False,
    unsafe_skip_review: bool = False,
    no_wait: bool = False,
    pool_probe: Callable[[PoolSlot], Any] | None = None,
    runner: Runner | None = None,
    case_runner: Callable[[CaseJob, PoolSlot], dict[str, Any]] | None = None,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
    cancel_check: CancelCheck | None = None,
    on_spawn: SpawnFn | None = None,
    on_reap: ReapFn | None = None,
    on_wait: Callable[[bool], None] | None = None,
) -> dict[str, Any]:
    """One run per requirement at a time; the lock guards evidence and skills."""
    with _run_lock(root, jira):
        return _req_test(
            root,
            jira,
            env=env,
            print_mode=print_mode,
            design_only=design_only,
            run_only=run_only,
            redesign=redesign,
            approve=approve,
            feedback=feedback,
            design_notes=design_notes,
            design_refs=design_refs,
            ingest=ingest,
            resume=resume,
            rerun_cases=rerun_cases,
            verify=verify,
            verify_only=verify_only,
            allow_unverified=allow_unverified,
            unsafe_skip_review=unsafe_skip_review,
            no_wait=no_wait,
            pool_probe=pool_probe,
            runner=runner,
            case_runner=case_runner,
            on_progress=on_progress,
            on_log=on_log,
            cancel_check=cancel_check,
            on_spawn=on_spawn,
            on_reap=on_reap,
            on_wait=on_wait,
        )


def _try_transition(
    root: Path,
    jira: str,
    event: str,
    on_log: LogFn | None = None,
    **fields: Any,
) -> None:
    """Advance the QA state machine without letting bookkeeping fail the step.

    Evidence files already hold the facts, and `derive_phase` is what gates read;
    `transition` writes the recorded phase and its guard is surfaced as
    `phase_drift` when it disagrees. A rejection is therefore a signal to inspect,
    never a reason to fail an otherwise-valid approve/verify/run — but it is
    logged rather than swallowed silently, so a wrong move is not invisible.
    """
    try:
        qa_st.transition(root, jira, event, **fields)
    except TestRejected as e:
        if on_log is not None:
            on_log(f"状态机未记录 {event}：{e}（以证据为准）\n")


def _req_test(
    root: Path,
    jira: str,
    *,
    env: str | None = None,
    print_mode: bool = False,
    design_only: bool = False,
    run_only: bool = False,
    redesign: bool = False,
    approve: bool = False,
    feedback: str | None = None,
    design_notes: str | None = None,
    design_refs: list[str] | str | None = None,
    ingest: bool = True,
    resume: bool | None = None,
    rerun_cases: list[str] | None = None,
    verify: bool | None = None,
    verify_only: bool = False,
    allow_unverified: bool = False,
    unsafe_skip_review: bool = False,
    no_wait: bool = False,
    pool_probe: Callable[[PoolSlot], Any] | None = None,
    runner: Runner | None = None,
    case_runner: Callable[[CaseJob, PoolSlot], dict[str, Any]] | None = None,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
    cancel_check: CancelCheck | None = None,
    on_spawn: SpawnFn | None = None,
    on_reap: ReapFn | None = None,
    on_wait: Callable[[bool], None] | None = None,
) -> dict[str, Any]:
    if design_only and run_only:
        raise TestRejected("--design-only and --run-only are mutually exclusive")
    if approve and (design_only or run_only):
        raise TestRejected("--approve cannot be combined with --design-only or --run-only")
    if verify_only and (design_only or run_only or redesign or approve or feedback or rerun_cases):
        raise TestRejected(
            "--verify-only cannot be combined with "
            "--design-only/--run-only/--redesign/--approve/--feedback/--rerun-case"
        )
    rerun_ids = {str(c).strip() for c in (rerun_cases or []) if str(c).strip()}
    if rerun_ids and (design_only or run_only or redesign or approve or feedback):
        raise TestRejected(
            "rerun_cases cannot be combined with design/run-only/redesign/approve/feedback"
        )
    qa = paths.qa_dir(root, jira)
    qa.mkdir(parents=True, exist_ok=True)
    cases = discover_cases(qa)
    had_cases = bool(cases)
    # Resolve the run to amend before loading config so the env can default to
    # the one that run actually used. No run yet means this click opens the
    # round and runs only the selected cases.
    rerun_run: tuple[str, Path, str] | None = None
    selective_fresh = False
    if rerun_ids:
        known = {c.id for c in cases}
        unknown = sorted(rerun_ids - known)
        if unknown:
            raise TestRejected(
                f"unknown case(s): {', '.join(unknown)}; "
                f"known: {', '.join(sorted(known)) or '(none)'}"
            )
        rerun_run = find_run_for_rerun(qa, rerun_ids)
        if rerun_run is None:
            selective_fresh = True
        elif not env and rerun_run[2]:
            env = rerun_run[2]
    # A case that is joining a round already in progress is diagnostic, same
    # as a re-run. The first case of a new round still waits for open tickets.
    _gate(
        root,
        jira,
        waive_open_tickets=bool(rerun_run is not None or redesign),
    )
    cfg = load_qa_config(root, env, jira)
    try:
        qa_yaml_mtime = paths.qa_yaml(root).stat().st_mtime
    except OSError:
        qa_yaml_mtime = None
    write_context_md(root, jira, cfg)
    notes_text = (design_notes or "").strip()
    try:
        ref_urls = qa_design_refs.normalize_refs(design_refs)
    except ValueError as e:
        raise TestRejected(str(e)) from e
    if run_only and not cases:
        raise TestRejected(f"{jira} has no qa/cases; cannot --run-only")
    feedback_text = feedback.strip() if feedback else ""
    review_bypassed = False
    if approve and (redesign or feedback_text):
        raise TestRejected("--approve cannot be combined with --redesign or --feedback")
    if approve and not had_cases:
        # Approval must be a human act on cases that were already shown; a
        # design run in the same invocation would self-approve unseen cases.
        raise TestRejected(
            f"{jira} has no cases to approve; run `dev-yard req test {jira}` "
            "first, review the cases, then --approve"
        )
    # Approve only records a decision. Design, verify, and execution all need
    # a live environment, usable accounts, and a reachable database first.
    review_only = bool(
        approve
        and not design_only
        and not run_only
        and not redesign
        and not verify_only
        and not rerun_ids
        and not feedback_text
    )
    if not review_only:
        from dev_yard.qa_ready import assert_ready

        ready = assert_ready(root, jira, cfg, cases)
        if ready is not None and on_log is not None:
            on_log(
                "前置检查通过："
                + "，".join(
                    f"{step.get('step')}={step.get('status')}" for step in ready.get("steps") or []
                )
                + "\n"
            )
    # M6b: a run that ended in case-defects leaves them recorded in state.yaml.
    # On the next design invocation, recycle them automatically — fix the seed,
    # not the tested point — unless the human asked for something else.
    auto_recycled_ids = qa_st.triage(root, jira)["auto_recycled"]
    auto_recycled_now = False
    if (
        auto_recycled_ids
        and not run_only
        and not verify_only
        and not redesign
        and not approve
        and not rerun_ids
        and not feedback_text
    ):
        redesign = True
        auto_recycled_now = True
        feedback_text = (
            "上一轮 run 判定以下用例为用例/种子缺陷（case-defect），"
            "请只修 setup / verify.sql / 依赖声明，保持用例正文与预期不变："
            + "、".join(auto_recycled_ids)
        )
        if on_log is not None:
            on_log(f"自动回流 case-defect：{', '.join(auto_recycled_ids)}\n")
    bodies_before = cases_fingerprint(qa, scope="bodies")
    need_design = (
        (not run_only) and not verify_only and (redesign or not cases or design_pending(qa))
    )
    if notes_text or ref_urls:
        if run_only or verify_only or approve:
            raise TestRejected(
                "--design-notes/--design-ref 只能在设计时使用（--design-only、--redesign，或尚无用例）"
            )
        need_design = True
    if feedback_text:
        if run_only:
            raise TestRejected("--feedback cannot be used with --run-only")
        if not need_design:
            raise TestRejected("--feedback requires --redesign")
    verify_enabled = verify is not False
    if verify_only and not cases:
        raise TestRejected(f"{jira} has no qa/cases; cannot --verify-only")
    history_text = ""
    if cases:
        try:
            history_text = render_prior_defects(prior_defects(qa, cases))
        except Exception:  # noqa: BLE001 — history is advisory, never block design
            history_text = ""
    design_runner: Runner | None = runner
    if need_design and on_log is not None:
        try:
            warn = _permission_gap_warning(root, jira, cfg)
        except Exception:  # noqa: BLE001 — advisory only, never block design
            warn = None
        if warn:
            on_log(warn + "\n")

    def _ensure_design_runner() -> Runner:
        nonlocal design_runner
        if design_runner is None:
            design_runner = get_runner(
                root,
                "qa-design",
                print_mode=print_mode,
                spec=load_registry(root)["qa-design"],
                provider=cfg.design_provider,
                model=cfg.design_model,
                on_spawn=on_spawn,
                on_reap=on_reap,
            )
        return design_runner

    if need_design:
        _raise_if_cancelled(cancel_check, "qa-design")
        qa_design_refs.materialize(qa, notes=notes_text or None, refs=ref_urls)
        if cfg.design_probe_attempts > 0 and not had_cases and not feedback_text and not redesign:
            from dev_yard.qa_probe import run_probes

            probe_extra = (
                "探针轮。不要写 qa/cases/。\n"
                "只写 qa/probes/*.sql：每文件一条只读 SELECT/WITH，"
                "首行可选 `-- probe: <catalog>`。写完即停。\n"
                "必须覆盖：每个 catalog 的方言/版本；凡当部门/角色 id 用的常量，"
                "查它在目标表里有没有行；凡 setup 会拿来做计数自证的口径，"
                "先查出当前基线（不要假设库是空的）。\n"
            )
            spec = load_registry(root)["qa-design"]
            probe_prompt = session_prompt_for(spec, root, jira, extra=probe_extra)
            _mark_design_pending(qa)
            probe_result = _ensure_design_runner().start(
                probe_prompt,
                root,
                attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]),
            )
            _raise_if_cancelled(cancel_check, "qa-design")
            if probe_result.ok:
                try:
                    run_probes(qa, cfg, on_log=on_log)
                except Exception as e:  # noqa: BLE001
                    if on_log is not None:
                        on_log(f"探针执行失败（继续设计）：{e}\n")
            elif on_log is not None:
                on_log(
                    f"探针轮没有得到模型回复，继续设计："
                    f"{probe_result.summary or probe_result.exit_code}\n"
                )
        prompt = _design_prompt(
            root,
            jira,
            cfg,
            feedback=feedback_text or None,
            history=history_text or None,
        )
        _mark_design_pending(qa)
        result = _ensure_design_runner().start(
            prompt,
            root,
            attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]),
        )
        _raise_if_cancelled(cancel_check, "qa-design")
        if not result.ok:
            raise TestRejected(f"qa-design failed: {result.summary or result.exit_code}")
        cases = discover_cases(qa)
        _finish_design(qa)
        if feedback_text:
            if auto_recycled_now and (cases_fingerprint(qa, scope="bodies") == bodies_before):
                # Only seeds/verify moved: the tested point is unchanged, so the
                # previous approval carries over (M6).
                mark_machine_fixed(qa, feedback_text)
            else:
                reject_cases(qa, feedback_text)
        elif redesign:
            # A redesign supersedes a doc-change stale flag; the changed case
            # fingerprint still forces a fresh review.
            clear_stale(qa)

    if auto_recycled_now:
        # Consume the case-defect list so a later plain `req test` does not keep
        # forcing a redesign; the next run records a fresh triage anyway.
        prev_triage = qa_st.triage(root, jira)
        qa_st.record_triage(root, jira, prev_triage["pending"], [])

    # M7: static contract lint with one bounded design fix pass, before verify.
    # A seed/contract mistake should not cost a human review or a run.
    if cases and not run_only and not rerun_ids and not verify_only and not allow_unverified:
        problems = lint_cases(root, jira, cfg, cases)
        account_gaps = [p for p in problems if _is_account_gap(p)]
        problems = [p for p in problems if p not in account_gaps]
        if account_gaps and on_log is not None:
            on_log("缺账号不回灌设计，保持未覆盖：" + "；".join(account_gaps) + "\n")
        if problems:
            if on_log is not None:
                on_log("用例契约检查未通过，回灌 design 修正：" + "；".join(problems) + "\n")
            _raise_if_cancelled(cancel_check, "qa-design")
            fix_prompt = _design_prompt(
                root,
                jira,
                cfg,
                verify_feedback=(
                    "宿主静态契约检查未通过，请只修正这些问题：\n"
                    + "\n".join(f"- {p}" for p in problems)
                ),
                contract_fix=True,
            )
            _mark_design_pending(qa)
            result = _ensure_design_runner().start(
                fix_prompt,
                root,
                attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]),
            )
            _raise_if_cancelled(cancel_check, "qa-design")
            if not result.ok:
                # The first design already finished. A stalled patch must not
                # leave the pending sentinel, or the next click redesigns
                # every case on the same model.
                if read_design_marker(qa) is None:
                    raise TestRejected(
                        f"qa-design 契约修正失败: {result.summary or result.exit_code}"
                    )
                (qa / _DESIGN_PENDING).unlink(missing_ok=True)
                if on_log is not None:
                    on_log(
                        "契约修正没有得到模型回复，已保留本轮用例并继续数据核实："
                        f"{result.summary or result.exit_code}\n"
                        "未改掉的契约问题仍会挡住审核。\n"
                    )
            else:
                cases = discover_cases(qa)
                _finish_design(qa)
                # The changed case set invalidates any prior approval; the review
                # gate holds it for a fresh look (no need to force `rejected`).

    # M1/M3: prove each case's declared data prerequisites host-side. Failures
    # loop back into design (bounded); the final verdict is written so the gate
    # and the human can see exactly what was checked.
    verify_results: dict[str, VerifyResult] = {}
    verify_fingerprint = cases_fingerprint(qa) if cases else ""
    pre_verify_fingerprint = verify_fingerprint
    if verify_enabled and cases and not run_only and not rerun_ids:
        _try_transition(root, jira, "verify_start", on_log=on_log)
        if verify_only:
            verify_results = verify_cases(
                root,
                jira,
                cfg,
                cases,
                fingerprint=verify_fingerprint,
                on_log=on_log,
                cancel_check=cancel_check,
            )
        else:
            verify_results = _verify_loop(
                root,
                jira,
                cfg,
                qa,
                cases,
                history_text=history_text,
                design_runner=_ensure_design_runner,
                on_log=on_log,
                cancel_check=cancel_check,
            )
            cases = discover_cases(qa)
            verify_fingerprint = cases_fingerprint(qa)
        write_summary(root, jira, verify_results, verify_fingerprint)
        if on_log is not None:
            on_log(describe(verify_results) + "\n")
        blocked_path = write_blocked(root, jira, verify_results)
        if blocked_path is not None and on_log is not None:
            on_log(f"数据核实未通过，design-blocked 清单：{blocked_path}\n")
        _try_transition(root, jira, "verify_done", on_log=on_log)
    verify_info = (
        verify_view(qa, verify_fingerprint) if cases else {"present": False, "stale": False}
    )
    # Advisory coverage: a change point no case claims. Surfaced at review time
    # so a human can ask for the missing case before approving.
    uncovered = uncovered_changes(qa, cases)
    if uncovered and on_log is not None:
        on_log(f"提示：改动点未被任何用例 covers：{', '.join(uncovered)}\n")
    # M7: static contract checks, before a human is asked to approve.
    lint_problems = lint_cases(root, jira, cfg, cases) if cases else []
    if lint_problems and on_log is not None:
        on_log("用例契约检查：" + "；".join(lint_problems) + "\n")

    if design_only:
        failed_ids = list((verify_info or {}).get("failed") or [])
        return {
            "jira": jira,
            "design_only": True,
            "design_blocked": bool(failed_ids),
            "cases": len(cases),
            "questions": _count_open_questions(qa),
            "open_questions": open_questions_payload(qa),
            "review": review_payload(qa),
            "verify": verify_info,
            "uncovered_changes": uncovered,
            "lint": lint_problems,
        }
    if verify_only:
        return {
            "jira": jira,
            "verify_only": True,
            "cases": len(cases),
            "verify": verify_info,
        }
    if not cases:
        raise TestRejected(f"{jira} qa-design produced no cases")
    require_verify = bool(cfg.design_verify_required and verify_enabled)
    if approve:
        if verify_fingerprint != pre_verify_fingerprint:
            # The verify loop redesigned cases in this invocation; approving them
            # here would bless cases the person has not seen.
            return {
                "jira": jira,
                "awaiting_review": True,
                "cases": len(cases),
                "questions": _count_open_questions(qa),
                "open_questions": open_questions_payload(qa),
                "reason": "数据核实失败已自动重做用例，请复核后重新 --approve",
                "review": review_payload(qa),
                "verify": verify_info,
                "uncovered_changes": uncovered,
                "lint": lint_problems,
            }
        if lint_problems and not allow_unverified:
            raise TestRejected(
                "用例契约检查未通过："
                + "；".join(lint_problems)
                + "；修好后重跑设计，或加 --allow-unverified 越权"
            )
        ok, why = verify_gate(
            qa,
            cases_fingerprint(qa),
            required=require_verify,
            allow_unverified=allow_unverified,
        )
        if not ok:
            raise TestRejected(
                f"--approve 被拒绝：{why}；修好后重跑设计，或加 --allow-unverified 越权"
            )
        approve_cases(qa)
        # M1: record the human gate as an explicit state-machine transition.
        _try_transition(root, jira, "approve", on_log=on_log)
        # Approval is a marker, never an execution: running the cases is a
        # separate `--run-only` / `qa-run` step so a review cannot silently
        # trigger a run (and so accounts can be configured in between).
        return {
            "jira": jira,
            "approved": True,
            "cases": len(cases),
            "review": review_payload(qa),
            "verify": verify_info,
        }
    elif not rerun_ids or selective_fresh:
        can_run, hold_reason = review_gate(
            qa,
            require_verify=require_verify,
            allow_unverified=allow_unverified,
        )
        if not can_run and selective_fresh:
            if not unsafe_skip_review:
                raise TestRejected(f"{hold_reason}；先审核用例，或加 --unsafe-skip-review")
            review_bypassed = True
            if on_log is not None:
                on_log(f"警告：单独执行绕过用例审核（{hold_reason}）\n")
        elif not can_run and run_only:
            # `--run-only` is the one path that can run unapproved cases. Make
            # the bypass an explicit, auditable opt-in instead of a silent one.
            if not unsafe_skip_review:
                raise TestRejected(
                    f"{hold_reason}；--run-only 会绕过人工审核，确认后加 --unsafe-skip-review 重跑"
                )
            review_bypassed = True
            if on_log is not None:
                on_log(f"警告：--run-only 绕过用例审核（{hold_reason}）\n")
        elif not can_run:
            return {
                "jira": jira,
                "awaiting_review": True,
                "cases": len(cases),
                "questions": _count_open_questions(qa),
                "open_questions": open_questions_payload(qa),
                "reason": hold_reason,
                "review": review_payload(qa),
                "verify": verify_info,
                "uncovered_changes": uncovered,
                "lint": lint_problems,
            }
    if allow_unverified:
        _mark_design_blocked(cases, qa, cases_fingerprint(qa))

    _check_case_repos(root, cases)
    evidence = qa / "evidence"
    case_ids = {c.id for c in cases}
    incomplete = find_incomplete_run(qa, case_ids, cfg.active_env)
    if resume is True and incomplete is None and rerun_run is None:
        raise TestRejected(
            f"--resume: no incomplete run for {jira} in env {cfg.active_env}; "
            "use --fresh to start a new run"
        )
    # M10: a finished run whose cases failed/blocked is amended in place by
    # default, so fixing B tickets only re-runs what actually broke.
    if (
        rerun_run is None
        and incomplete is None
        and resume is None
        and not redesign
        and not run_only
        and not rerun_ids
        and cfg.run_incremental
    ):
        prev = _latest_retryable_run(qa, case_ids, cfg.active_env)
        if prev is not None and prev[1]:
            retryable = latest_retryable(qa, cases, cfg.active_env)
            # M10: a fix that touched a change point (meta.yaml `changes[].ref`)
            # re-runs its covering cases too, even ones that passed last time.
            touched = affected_covers(root, jira, cases, prev[0])
            extra = sorted(touched - set(retryable))
            if extra:
                retryable = sorted(set(retryable) | touched)
                if on_log is not None:
                    on_log(f"受影响用例（covers 命中本次 diff）：{', '.join(extra)}\n")
            # Amend the env-matched run we derived from (its case set is exactly
            # the current one), so an env switch never reuses a stale run.
            rerun_run = (prev[0].name, prev[0], cfg.active_env)
            rerun_ids = set(retryable)
            if on_log is not None:
                on_log(
                    f"增量重跑 {len(retryable)} 条"
                    f"（上一轮 failed/blocked + 受影响 covers）："
                    f"{', '.join(sorted(retryable))}\n"
                )
    if rerun_run is not None:
        run_id, run_dir, _ = rerun_run
        _reject_selective(cases, rerun_ids, run_dir)
        reset = reset_cases_in_run(run_dir, rerun_ids, case_ids)
        _apply_resume(cases, run_dir)
        queued = [job.id for job in cases if job.id in rerun_ids and job.state == "held"]
        for job in cases:
            if job.id in queued:
                job.state = "pending"
                job.reason = ""
        if not reset and not queued:
            raise TestRejected(
                f"nothing to re-run in run {run_id} for "
                f"{', '.join(sorted(rerun_ids))}; already queued or running"
            )
        if on_log is not None and reset:
            on_log(f"reset {', '.join(sorted(reset))} for re-run in run {run_id}")
        if on_log is not None and queued:
            on_log(f"排队 {', '.join(sorted(queued))}，补进 run {run_id}\n")
        resuming = True
    elif (
        incomplete is not None
        and not selective_fresh
        and (resume is True or (resume is None and not redesign))
    ):
        # A named case that no run contains must not resume a stray evidence
        # dir (no progress.yaml still counts as running) and schedule every case.
        run_id, run_dir = incomplete
        if run_only:
            released = _release_held_cases(run_dir)
            if released and on_log is not None:
                on_log(f"执行用例补跑未排队 {len(released)} 条：{', '.join(released)}\n")
        if on_log is not None:
            on_log(f"resuming run {run_id}")
        _apply_resume(cases, run_dir)
        resuming = True
    else:
        if selective_fresh:
            _hold_unselected(cases, rerun_ids)
            if on_log is not None:
                on_log(f"只执行 {', '.join(sorted(rerun_ids))}，其余留在本轮未排队\n")
        run_id, run_dir = _claim_run_dir(evidence)
        resuming = False
    aliases = _involved_aliases(root, jira)
    # An amendment (`rerun_run`) re-runs cases after a fix landed, so HEAD has
    # legitimately moved: rebaseline here. An interrupted-resume keeps the run's
    # original baseline so a mutation the crashed attempt left behind is caught.
    amending = rerun_run is not None
    tree_before: dict[str, dict[str, str]] = {}
    heads_before: dict[str, str] = {}
    baseline_dir = run_dir / "repo-baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        snap = _tree_state(wt)
        if snap is None:
            raise TestRejected(f"cannot read `git status` in {wt}; aborting before the run")
        baseline_json = baseline_dir / f"{alias}.json"
        baseline_txt = baseline_dir / f"{alias}.txt"
        # On an interrupted resume, compare against the run's *original* baseline
        # so a mutation left behind by the crashed attempt is still caught. A
        # fresh run — or an amendment after a fix — records a new baseline.
        if resuming and not amending and baseline_json.is_file():
            try:
                loaded = json.loads(baseline_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = None
            if isinstance(loaded, dict):
                tree_before[alias] = {str(k): str(v) for k, v in loaded.items()}
            else:
                tree_before[alias] = snap
        else:
            tree_before[alias] = snap
            baseline_json.write_text(
                json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if amending or not baseline_txt.is_file():
            text = _porcelain(wt)
            baseline_txt.write_text(text + ("\n" if text else ""), encoding="utf-8")
        # A commit leaves `git status` clean, so the porcelain diff alone cannot
        # see it; pin HEAD as a second baseline.
        head_path = baseline_dir / f"{alias}.head"
        if resuming and not amending and head_path.is_file():
            heads_before[alias] = head_path.read_text(encoding="utf-8").strip()
        else:
            sha = _head_sha(wt) or ""
            heads_before[alias] = sha
            head_path.write_text(sha, encoding="utf-8")
    root_png_before = _root_png_names(root)
    _check_case_accounts(cfg, cases, jira)
    # Resolve the default account so context/login use a real name. Concurrent
    # cases may share it unless qa.yaml serialize_accounts is true.
    for job in cases:
        resolved = _case_account(cfg, job)
        if resolved in cfg.env.accounts:
            job.account = resolved
    auth_failures = _preload_auth(root, cfg, _used_accounts(cfg, cases), on_log, cases=cases)
    if auth_failures:
        stamp = now_iso()
        for job in cases:
            if job.state == "held":
                continue
            why = _auth_failure_for(root, cfg, job, auth_failures)
            if why is not None:
                job.state = "blocked"
                job.reason = f"auth failed: {why}"
                job.blocked_class = "auth"
                job.ended_at = stamp

    pools = [PoolSlot.from_worker(w) for w in cfg.workers]
    # M3: optionally probe each pool and drop the ones that cannot run.
    if cfg.run_pool_preflight or pool_probe is not None:
        probe = pool_probe or (
            lambda p: _real_pool_probe(root, p, on_spawn=on_spawn, on_reap=on_reap)
        )
        kept: list[PoolSlot] = []
        for p in pools:
            try:
                res = probe(p)
            except Exception as e:  # noqa: BLE001 — a probe failure is just a drop
                res = (False, str(e))
            ok, reason = res if isinstance(res, tuple) else (bool(res), "")
            if ok:
                kept.append(p)
            else:
                qa_st.record_pools(root, jira, {p.id: "quarantined"})
                if on_log is not None:
                    on_log(f"模型池预检失败，隔离 {p.id}：{reason}\n")
        if not kept:
            host_left = any(
                (c.driver or "browser") == "host" and c.state not in TERMINAL for c in cases
            )
            if not host_left:
                raise TestRejected("所有模型池预检失败；修好 provider/额度后重跑")
            pools = []
            if on_log is not None:
                on_log("模型池预检全部失败，本轮只执行 driver: host\n")
        else:
            pools = kept
    qa_st.record_pools(root, jira, {p.id: "healthy" for p in pools})
    if on_log is not None:
        on_log("本轮使用启动时的 worker 配置\n")
    progress_path = run_dir / "progress.yaml"
    script_lock = Lock()
    env_fault: dict[str, Any] | None = None
    fuse_class: str | None = None
    fuse_streak = 0
    from dev_yard.script_exec import FUSE_CLASSES

    executor, site_faults = ping_case_sites(root, jira, cfg, cases)
    if executor is not None and executor.cross_site_warning and on_log is not None:
        on_log(executor.cross_site_warning + "\n")
    serial = not bool(getattr(cfg.env.exec_cfg, "parallel", False))
    hold_setup = serial or (executor is not None and executor.use == "jms-k8s")

    def ping() -> None:
        _write_progress(
            progress_path,
            run_id,
            cfg.active_env,
            pools,
            cases,
            on_progress,
            on_log,
            env_fault=env_fault,
        )

    if site_faults:
        first_fault = next(iter(site_faults.values()))
        env_fault = {
            "class": first_fault.get("class") or "unreachable",
            "message": "; ".join(
                f"{site or '(default)'}: {fault.get('message')}"
                for site, fault in site_faults.items()
            ),
        }
        stamp = now_iso()
        for job in cases:
            if job.state == "held":
                continue
            fault = site_faults.get(exec_site_for_job(root, cfg, job) or "")
            if fault and case_needs_exec_site(job) and job.state not in TERMINAL:
                job.state = "blocked"
                job.reason = f"env fault: {fault.get('message')}"
                job.blocked_class = "env"
                job.ended_at = stamp
        if on_log is not None:
            on_log(f"env fault: {env_fault['message']}\n")

    ping()

    run_wide_fault = False

    def _mark_env_fault(message: str, cls: str) -> None:
        nonlocal env_fault, run_wide_fault
        run_wide_fault = True
        env_fault = {"class": cls, "message": message}
        stamp = now_iso()
        for c in cases:
            if c.state == "held":
                continue
            if case_needs_exec_site(c) and c.state not in TERMINAL:
                c.state = "blocked"
                c.reason = f"env fault: {message}"
                c.blocked_class = "env"
                c.ended_at = stamp

    def default_case_runner(job: CaseJob, slot: PoolSlot) -> dict[str, Any]:
        nonlocal fuse_streak, fuse_class
        from dev_yard.qa_seeds import (
            apply_seed_placeholders,
            parse_qa_seeds,
            seed_action_text,
            seeds_from_deps,
            write_case_seeds,
        )

        spec = load_registry(root)["qa-run"]
        case_dir = run_dir / job.id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "screenshots").mkdir(exist_ok=True)
        result_path = case_dir / "result.yaml"

        def _echo(line: str) -> None:
            if on_log is not None:
                on_log(line if line.endswith("\n") else line + "\n")

        setup_failed: str | None = None
        seed_gap: str | None = None
        seed_gap_kind = "case"
        blank_aliases: list[str] = []
        seeds: dict[str, dict[str, str]] = {}
        if job.setup:
            ctx = script_lock if hold_setup else _nullcontext()
            with ctx:
                own_site = exec_site_for_job(root, cfg, job) or ""
                site_down = case_needs_exec_site(job) and (
                    (run_wide_fault and env_fault is not None) or own_site in site_faults
                )
                if site_down:
                    message = (
                        env_fault.get("message")
                        if run_wide_fault and env_fault is not None
                        else site_faults.get(own_site, {}).get("message")
                    )
                    setup_failed = f"env fault: {message}"
                else:
                    try:
                        out = run_case_script(
                            root,
                            jira,
                            cfg,
                            job,
                            "setup",
                            on_log=on_log,
                            executor=executor,
                        )
                        own_seeds = parse_qa_seeds(out or "")
                        try:
                            write_case_seeds(paths.qa_dir(root, jira), job.id, own_seeds)
                        except OSError:
                            pass
                        seeds = seeds_from_deps(paths.qa_dir(root, jira), job, cases)
                        seeds.update(own_seeds)
                        if out and on_log is not None:
                            on_log(out[-500:])
                        fuse_streak = 0
                        fuse_class = None
                    except TestRejected as e:
                        setup_failed = f"setup failed: {e}"
                        cls = str(getattr(e, "error_class", None) or "")
                        if cls == "auth" and executor is not None and executor.use == "jms-k8s":
                            _mark_env_fault(str(e), cls)
                        elif cls in FUSE_CLASSES:
                            if cls == fuse_class:
                                fuse_streak += 1
                            else:
                                fuse_class = cls
                                fuse_streak = 1
                            if fuse_streak >= 2:
                                _mark_env_fault(str(e), cls)

        if setup_failed is None and not job.setup:
            if not seeds:
                seeds = seeds_from_deps(paths.qa_dir(root, jira), job, cases)
            from dev_yard.qa_verify import read_existing_seeds

            seed_gap, seed_gap_kind, blank_aliases = read_existing_seeds(
                cfg, job, seeds, on_log=on_log
            )

        if seed_gap:
            got = {
                "status": "blocked",
                "reason": (
                    f"env fault: {seed_gap}"
                    if seed_gap_kind == "env"
                    else f"case-defect: {seed_gap}"
                ),
                "blocked_class": "env" if seed_gap_kind == "env" else "case-defect",
                "repo": job.repo,
                "model": slot.model,
                "provider": slot.provider,
            }
        elif setup_failed is not None:
            got = {
                "status": "blocked",
                "reason": setup_failed,
                "blocked_class": "env",
                "repo": job.repo,
                "model": slot.model,
                "provider": slot.provider,
            }
        elif (job.driver or "browser") == "host":
            if not seeds:
                seeds = seeds_from_deps(paths.qa_dir(root, jira), job, cases)
            result_path.unlink(missing_ok=True)
            got = host_case_verdict(cfg, job, seeds, on_log=on_log)
            result_path.write_text(
                yaml.safe_dump(
                    {"case": job.id, "title": job.title, **got},
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
        else:
            # A resumed case dir may hold the interrupted attempt's result;
            # never let a stale file stand in for this run's outcome.
            result_path.unlink(missing_ok=True)
            if not seeds:
                seeds = seeds_from_deps(paths.qa_dir(root, jira), job, cases)
            raw_case = Path(job.path).read_text(encoding="utf-8") if job.path else job.body
            rendered, _known = apply_seed_placeholders(raw_case, seeds)
            _scoped, missing_seeds = apply_seed_placeholders(seed_action_text(raw_case), seeds)
            if missing_seeds:
                from dev_yard.qa_seeds import explain_missing_seeds

                got = {
                    "status": "blocked",
                    "reason": "case-defect: 页面步骤"
                    + explain_missing_seeds(missing_seeds, blank_aliases, qa_seed=True),
                    "blocked_class": "case-defect",
                    "repo": job.repo,
                    "model": slot.model,
                    "provider": slot.provider,
                }
                result_path.write_text(
                    yaml.safe_dump(
                        {"case": job.id, "title": job.title, **got},
                        sort_keys=False,
                        allow_unicode=True,
                    ),
                    encoding="utf-8",
                )
            else:
                prompt = _run_prompt(root, jira, cfg, job, run_id, case_text=rendered)
                argv = pi_argv(
                    root=root,
                    bundle="qa-run",
                    prompt=None,
                    print_mode=True,
                    spec=spec,
                    provider=slot.provider,
                    model=slot.model,
                    attach=attachments.list_images(root, jira),
                )
                _raise_if_cancelled(cancel_check, f"qa-run {job.id}")
                auth_env = _case_auth_env(root, cfg, job)
                code, raw = run_pi_print_tracked(
                    argv,
                    root,
                    prompt,
                    on_line=_echo,
                    on_spawn=on_spawn,
                    on_reap=on_reap,
                    **({"env": auth_env} if auth_env else {}),
                )
                _raise_if_cancelled(cancel_check, f"qa-run {job.id}")
                if code != 0 and not result_path.is_file():
                    reason, detail = diagnose_pi_exit(code, raw)
                    if detail and on_log is not None:
                        on_log(f"pi exit {code} ({job.id}): {detail}\n")
                    got = {
                        "status": "blocked",
                        "reason": reason,
                        "blocked_class": "env",
                        "repo": job.repo,
                        "model": slot.model,
                        "provider": slot.provider,
                    }
                else:
                    got = _read_case_result(result_path, job, slot)
                    if not got.get("model"):
                        got["model"] = slot.model
                    if not got.get("provider"):
                        got["provider"] = slot.provider
                    if normalize_status(got.get("status")) == "passed":
                        problems = recheck_db_assertions(cfg, job, got, on_log=on_log, seeds=seeds)
                        mismatches = [p for p in problems if p.get("kind") == "mismatch"]
                        unverified = [p for p in problems if p.get("kind") == "unverified"]
                        if mismatches:
                            detail = "; ".join(
                                f"expected={p.get('expected')!r} actual={p.get('actual')!r}"
                                for p in mismatches
                            )
                            got["status"] = "failed"
                            got["reason"] = f"host-recheck-mismatch: {detail}"
                            if not got.get("defect_class"):
                                got["defect_class"] = "unclassified"
                            bad_sql = {
                                str(p.get("sql") or "").strip()
                                for p in mismatches
                                if str(p.get("sql") or "").strip()
                            }
                            for item in got.get("assertions") or []:
                                if (
                                    isinstance(item, dict)
                                    and str(item.get("sql") or "").strip() in bad_sql
                                ):
                                    item["status"] = "failed"
                        if unverified:
                            soft_sql = {
                                str(p.get("sql") or "").strip()
                                for p in unverified
                                if str(p.get("sql") or "").strip()
                            }
                            for item in got.get("assertions") or []:
                                if (
                                    isinstance(item, dict)
                                    and str(item.get("sql") or "").strip() in soft_sql
                                ):
                                    item["host_recheck"] = "unverified"
                            if on_log is not None:
                                on_log(
                                    f"db 断言未复核（{job.id}）：expected 与 worker actual "
                                    "都是句子，保留 worker 结论\n"
                                )
                        for p in problems:
                            if p.get("kind") == "error" and on_log is not None:
                                on_log(f"db 断言复核未执行（{job.id}）：{p.get('actual')}\n")
                    if raw and on_log is not None and code != 0:
                        on_log(raw[-500:])
        if job.cleanup:
            try:
                ctx = script_lock if serial else _nullcontext()
                with ctx:
                    run_case_script(
                        root,
                        jira,
                        cfg,
                        job,
                        "cleanup",
                        on_log=on_log,
                        executor=executor,
                    )
            except TestRejected as e:
                got = dict(got)
                if normalize_status(got.get("status")) == "passed":
                    # cleanup failure means polluting data we cannot trust.
                    got["status"] = "blocked"
                got["reason"] = ((got.get("reason") or "") + f" cleanup failed: {e}").strip()
        if result_path.is_file():
            persist_case_verdict(result_path, got)
        return got

    runner_fn = case_runner or default_case_runner
    run_cancelled = False
    try:
        # Seeds share one test DB across requirements, so a run must not overlap
        # another requirement's run/verification in the same env.
        with env_lock(
            root,
            cfg.active_env,
            what="qa run",
            wait_timeout=0 if no_wait else cfg.run_env_wait_timeout,
            on_wait=on_wait,
        ):
            # M1: the run is now the active phase. Record it *after* the env lock
            # so a queued job does not report `running` (and phase_drift) while
            # it is still waiting its turn. Also flips a resumed `paused` run
            # back to `running`.
            qa_run.set_status(run_dir, qa_run.RUNNING)
            _try_transition(root, jira, "run_start", on_log=on_log)
            run_cancelled = run_schedule(
                cases,
                pools,
                runner_fn,
                on_progress=ping,
                serialize_accounts=cfg.serialize_accounts,
                cancel_check=cancel_check,
                retry_attempts=cfg.run_retry_attempts,
                retry_backoff=cfg.run_retry_backoff,
                on_pool_trip=lambda pid, reason: qa_st.record_pools(
                    root, jira, {pid: "quarantined"}
                ),
            )
    finally:
        if executor is not None:
            executor.close()

    # Paused mid-run: record the lifecycle status, then bail before writing
    # result.yaml / ingesting, so the partial run is left resumable instead of
    # recorded as a real outcome. `paused` also keeps a restart from auto-
    # resuming a run the user stopped on purpose.
    if run_cancelled:
        qa_run.set_status(run_dir, qa_run.PAUSED)
    _raise_if_cancelled(cancel_check, "qa-run")

    for job in cases:
        if job.state in {"skipped", "blocked"} and job.ended_at:
            if not job.model:
                # A case that never got scheduled would have run on the pool
                # `pick_pool` prefers, so record that instead of an empty model.
                preferred = min(pools, key=lambda p: (p.priority, p.id))
                job.model = preferred.model
                job.provider = preferred.provider
            dest = run_dir / job.id / "result.yaml"
            if not dest.is_file():
                _write_skipped_result(dest, job)

    extra: list[str] = []
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        after_state = _tree_state(wt)
        before_state = tree_before.get(alias, {})
        if after_state is None:
            # `git status` broke after the run; treat as unsafe rather than
            # silently skipping the mutation check.
            extra.append(f"{alias}: git status unreadable after run")
            continue
        head_after = _head_sha(wt)
        before_head = heads_before.get(alias) or ""
        if before_head and head_after and head_after != before_head:
            extra.append(f"{alias}: HEAD moved {before_head[:8]}..{head_after[:8]}")
        for path, sig in after_state.items():
            if before_state.get(path) != sig:
                extra.append(path)
    new_root_png = _root_png_names(root) - root_png_before
    extra = sorted(dict.fromkeys(extra))

    summary = _summarize(cases)
    held_left = [c.id for c in cases if c.state == "held"]
    if held_left:
        # Keep the round open. result.yaml would mark it concluded, and the
        # next single-case click would have to start a second round.
        if extra:
            qa_run.set_status(run_dir, qa_run.PARTIAL)
            raise TestRejected(
                f"worker mutated worktree: {', '.join(extra[:5])}"
                + (" ..." if len(extra) > 5 else "")
            )
        qa_run.set_status(run_dir, qa_run.PARTIAL)
        qa_st.record(
            root,
            jira,
            phase="approved",
            last_run_id=run_id,
            last_verdict="",
        )
        ping()
        if on_log is not None:
            on_log(
                f"未排队 {len(held_left)} 条：{', '.join(held_left)}。"
                "再点「执行」补进本轮，或点「执行用例」把剩下的一起跑完。\n"
            )
        return {
            "jira": jira,
            "run_id": run_id,
            "env": cfg.active_env,
            "summary": summary,
            "ingested": False,
            "ingest_skipped": "partial",
            "cases": len(cases),
            "partial": True,
            "held": held_left,
        }
    run_doc: dict[str, Any] = {
        "run_id": run_id,
        "env": cfg.active_env,
        "workers": [
            {
                "id": w.id,
                "model": w.model,
                "concurrency": w.concurrency,
                "priority": w.priority,
            }
            for w in cfg.workers
        ],
        "cases": [
            {
                "case": c.id,
                "status": c.state,
                "repo": c.repo,
                "model": c.model,
                "reason": c.reason,
                "blocked_class": c.blocked_class,
                "defect_class": c.defect_class,
                "assertions": c.assertions,
            }
            for c in cases
        ],
        "summary": summary,
    }
    if qa_yaml_mtime is not None:
        try:
            changed_mtime = paths.qa_yaml(root).stat().st_mtime != qa_yaml_mtime
        except OSError:
            changed_mtime = False
        if changed_mtime:
            run_doc["config_snapshot"] = "startup"
            if on_log is not None:
                on_log("qa.yaml 在本轮运行中被修改，本轮仍使用启动时的 pool 配置\n")
    if env_fault:
        run_doc["env_fault"] = env_fault
    if extra:
        run_doc["mutation"] = extra
    if review_bypassed:
        run_doc["review_bypassed"] = True
    (run_dir / "result.yaml").write_text(
        yaml.safe_dump(run_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    ping()
    # Conclude the run *before* ingest / the mutation raise: once result.yaml is
    # written the run is over, and a crash in between must not leave an ingested
    # run recorded as still `running` (and therefore resumable).
    qa_run.set_status(
        run_dir,
        qa_run.CONCLUDED,
        outcome=(
            "failed"
            if extra or summary.get("failed")
            else "blocked"
            if summary.get("blocked")
            else "passed"
        ),
    )
    if extra:
        # Recorded above before raising, so the run is not lost.
        raise TestRejected(
            f"worker mutated worktree: {', '.join(extra[:5])}" + (" ..." if len(extra) > 5 else "")
        )

    case_payloads: list[dict[str, Any]] = []
    for c in cases:
        item = {
            "case": c.id,
            "title": c.title,
            "repo": c.repo,
            "covers": c.covers,
            "model": c.model,
            "provider": c.provider,
            "status": c.state,
            "reason": c.reason,
        }
        if c.defect_class:
            item["defect_class"] = c.defect_class
        if c.failure:
            item["failure"] = c.failure
        if c.assertions:
            item["assertions"] = c.assertions
        raw_path = run_dir / c.id / "result.yaml"
        if raw_path.is_file():
            try:
                raw = yaml.safe_load(raw_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                raw = {}
            if isinstance(raw, dict):
                if not item.get("repo"):
                    item["repo"] = str(raw.get("repo") or "")
                if raw.get("failure") and not item.get("failure"):
                    item["failure"] = raw.get("failure")
                if raw.get("title"):
                    item["title"] = raw.get("title")
                if raw.get("defect_class") and not item.get("defect_class"):
                    item["defect_class"] = str(raw.get("defect_class"))
                if isinstance(raw.get("assertions"), list):
                    item["assertions"] = raw["assertions"]
        case_payloads.append(item)

    ingested = False
    ingest_skipped = None
    if ingest:
        try:
            report = map_qa_result(run_doc, case_payloads)
        except ReportRejected as e:
            raise TestRejected(str(e)) from e
        if report is None:
            if summary.get("blocked"):
                ingest_skipped = "blocked"
            elif has_design_blocked_skip(case_payloads):
                ingest_skipped = "design-blocked"
            elif summary.get("passed"):
                ingest_skipped = "malformed"
            else:
                ingest_skipped = "no passed cases"
        else:
            repos = load_repos(root)
            for f in report.findings:
                if f.repo not in repos:
                    raise TestRejected(f"finding {f.id} repo {f.repo!r} is not a repos.yaml alias")
            accept_test_report(root, jira, report, spawn=False)
            ingested = True
    # Persist the ingest outcome so a blocked/skipped run is not a silent no-op.
    run_doc["ingested"] = ingested
    if ingest_skipped:
        run_doc["ingest_skipped"] = ingest_skipped
    (run_dir / "result.yaml").write_text(
        yaml.safe_dump(run_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    if new_root_png:
        shots = run_dir / "_root_png"
        shots.mkdir(exist_ok=True)
        for name in sorted(new_root_png):
            src = root / name
            dest = shots / name
            if src.is_file() and not dest.exists():
                try:
                    src.replace(dest)
                except OSError:
                    pass

    # M6b/M1: classify the non-passing cases and record the machine state so the
    # next invocation (or a restarted web server) knows what to do without a
    # human having to re-derive it from the report.
    triage = triage_buckets(case_payloads)
    if triage["pending"]:
        phase, event = "awaiting_triage", "run_failed"
    elif triage["auto_recycled"]:
        phase, event = "recycled", "run_recycled"
    elif summary.get("blocked"):
        phase, event = "running", "run_retryable"
    else:
        phase, event = "closed", "run_passed"
    # Record triage *before* the transition: `derive_phase` reads it to validate
    # the move is legal from the evidence's point of view (M1).
    qa_st.record_triage(root, jira, triage["pending"], triage["auto_recycled"])
    last_verdict = (
        "failed"
        if (summary.get("failed") or summary.get("blocked"))
        else "passed"
        if summary.get("passed")
        else ""
    )
    try:
        qa_st.transition(root, jira, event, last_run_id=run_id, last_verdict=last_verdict)
    except TestRejected as e:
        # The verdict is already in evidence/result.yaml; a state-machine
        # bookkeeping mismatch must never hide it, but do record the move and
        # say why the guard demurred.
        if on_log is not None:
            on_log(f"状态机未记录 {event}：{e}；已按证据写 phase={phase}\n")
        qa_st.record(
            root,
            jira,
            phase=phase,
            last_run_id=run_id,
            last_verdict=last_verdict,
        )

    return {
        "jira": jira,
        "run_id": run_id,
        "env": cfg.active_env,
        "summary": summary,
        "ingested": ingested,
        "ingest_skipped": ingest_skipped,
        "cases": len(cases),
        "triage": triage,
    }
