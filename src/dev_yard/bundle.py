"""Export/import a whole requirement as a self-contained bundle.

`req_export` packs a requirement's docs (`reqs/<JIRA>/`, minus git worktrees)
plus one `git bundle` per participating repo (the freeze branch and any
in-progress ticket branches) and a `manifest.yaml` describing how to put it
back. `req_import_bundle` restores it on another machine: it registers the
repos if needed, fetches the bundled branches, recreates the freeze/ticket
worktrees, and rewrites the machine-specific worktree paths in STATUS.yaml.

Design notes
------------
* Bundles are *thin* by default: each repo bundle excludes `origin/<default_base`
  so it only carries the requirement's own commits, and the target fetches the
  base from the remote. Pass `full=True` (CLI `--full`) for a self-contained
  bundle that restores offline; a repo whose base is only a local ref is always
  bundled in full, since that base cannot be fetched elsewhere.
* Export refuses to run on a dirty worktree unless `snapshot=True` (CLI
  `--snapshot`), which commits the pending edits first — so WIP is carried
  without silently mutating the source.
* STATUS.yaml records absolute worktree paths; export strips them and import
  recomputes them for the target root, so the bundle survives a different
  checkout location.
* A branch with no commits beyond its base produces no thin bundle (git refuses
  to write an empty bundle); import recreates it from the base ref instead.
* Accounts (`.yard-qa/.../accounts.yaml`) are plaintext secrets and are only
  included with `include_accounts=True`.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from dev_yard import gitops, paths
from dev_yard import status as st
from dev_yard.config import load_repos, normalize_git_url, resolve_freeze_branch
from dev_yard.fsutil import atomic_write_text
from dev_yard.tickets import load_tickets

Progress = gitops.Progress

EXPORT_FORMAT = "dev-yard-export"
EXPORT_VERSION = 1
MANIFEST_NAME = "manifest.yaml"
BUNDLES_DIR = "bundles"
DOCS_TOP = "reqs"
ACCOUNTS_NAME = "accounts.yaml"
WORKTREES_DIR = "worktrees"


class BundleError(ValueError):
    """A malformed or incompatible bundle."""


def _safe_rel(name: str) -> str:
    """A bundle-relative path that cannot escape the bundle directory."""
    rel = (name or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise BundleError(f"unsafe bundle path {name!r}")
    return "/".join(parts)


def _copy_tree(src: Path, dest: Path) -> None:
    """Copy a requirement doc tree, skipping git worktree checkouts."""
    dest.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        if child.name == WORKTREES_DIR:
            continue
        target = dest / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target, follow_symlinks=False)


def _unique_commits(source: Path, base_ref: str, ref: str) -> int:
    """Commits reachable from `ref` but not `base_ref`; 0 if either is unknown."""
    if gitops.rev_parse(source, ref) is None:
        return 0
    try:
        out = gitops.run(
            ["git", "rev-list", "--count", f"{base_ref}..{ref}"], cwd=source
        )
        return int(out.strip() or "0")
    except (gitops.GitError, ValueError):
        return 0


def _collect_children(
    root: Path, jira: str, source: Path, data: dict[str, Any], alias: str, freeze: str
) -> dict[str, str]:
    """In-progress ticket branches for one repo (ticket_id -> branch name).

    Prefers the branch a live child worktree is on; otherwise falls back to the
    canonical `ticket_branch_name`, so a branch whose worktree was removed is
    still carried. Only branches with commits beyond the freeze branch count.
    """
    from dev_yard.config import ticket_branch_name

    out: dict[str, str] = {}
    for tid, slot in st.tickets_map(data.get("tickets")).items():
        if str(slot.get("repo") or "") != alias:
            continue
        child = slot.get("child_worktree")
        name = gitops.checked_out_branch(Path(child)) if child else None
        name = name or ticket_branch_name(freeze, tid)
        if name != freeze and _unique_commits(source, freeze, name):
            out[tid] = name
    return out


def _dirty_worktrees(root: Path, jira: str, alias: str, children: dict[str, str]) -> list[str]:
    """Labels of freeze/child worktrees with uncommitted changes."""
    dirty: list[str] = []
    wt = paths.req_worktree(root, jira, alias)
    if (wt / ".git").exists() and gitops.has_changes(wt):
        dirty.append(alias)
    for tid in children:
        child = paths.child_worktree(root, jira, alias, tid)
        if child.exists() and gitops.has_changes(child):
            dirty.append(f"{alias}/{tid}")
    return dirty


def _snapshot_worktree(worktree: Path, label: str, jira: str, log: Progress) -> None:
    log(f"committing pending changes in {label}...")
    gitops.commit_all(worktree, f"chore(export): snapshot {jira} {label} before export")
    if gitops.has_changes(worktree):
        raise gitops.GitError(f"{label}: cannot snapshot uncommitted changes")


def req_export(
    root: Path,
    jira: str,
    dest: Path,
    *,
    include_accounts: bool = False,
    archive: bool = False,
    full: bool = False,
    snapshot: bool = False,
    force: bool = False,
    on_progress: Progress | None = None,
) -> Path:
    """Export requirement `jira` into `dest` (a directory, or a `.tar.gz`).

    `full` writes self-contained bundles (restorable offline); otherwise bundles
    are thin and need the remote base. `snapshot` commits pending worktree edits
    before bundling; without it a dirty worktree aborts the export.
    """
    req = paths.req_dir(root, jira)
    if not req.is_dir() or not paths.is_req_dir(req):
        raise FileNotFoundError(f"no requirement {jira}")

    def log(line: str) -> None:
        if on_progress:
            on_progress(line)

    with st.jira_lock(jira):
        data = st.load(root, jira)
        tickets = load_tickets(req)
        repos = load_repos(root)
        aliases = sorted(
            {t.repo for t in tickets if t.repo}
            | {str(a) for a in (data.get("repos") or []) if a}
        )
        freeze = resolve_freeze_branch(root, jira, data)

        staging = Path(tempfile.mkdtemp(prefix="dev-yard-export-"))
        try:
            _copy_tree(req, staging / DOCS_TOP / jira)
            _strip_paths(staging / DOCS_TOP / jira / "STATUS.yaml")

            # Pass 1: decide what to bundle and refuse a dirty export up front.
            plans: list[tuple[str, Any, Path, dict[str, str], Path]] = []
            dirty: list[str] = []
            for alias in aliases:
                repo = repos.get(alias)
                if repo is None:
                    raise ValueError(
                        f"repo alias {alias!r} is not registered; add it with `dev-yard repo add`"
                    )
                wt = paths.req_worktree(root, jira, alias)
                if not (wt / ".git").exists():
                    continue
                source = repo.source_path(root)
                children = _collect_children(root, jira, source, data, alias, freeze)
                found = _dirty_worktrees(root, jira, alias, children)
                if found and not snapshot:
                    dirty.extend(found)
                    continue
                plans.append((alias, repo, source, children, wt))
            if dirty:
                raise ValueError(
                    "uncommitted changes in " + ", ".join(dirty)
                    + "; commit/stash them or pass --snapshot to commit before export"
                )

            # Pass 2: snapshot pending edits, then bundle each repo.
            repo_entries: dict[str, Any] = {}
            for alias, repo, source, children, wt in plans:
                if snapshot:
                    if gitops.has_changes(wt):
                        _snapshot_worktree(wt, alias, jira, log)
                    for tid in children:
                        child = paths.child_worktree(root, jira, alias, tid)
                        if child.exists() and gitops.has_changes(child):
                            _snapshot_worktree(child, f"{alias}/{tid}", jira, log)

                base_ref = gitops.start_point(source, repo.default_base)
                refs = [
                    f"refs/heads/{freeze}",
                    *[f"refs/heads/{b}" for b in children.values()],
                ]
                head_sha = gitops.rev_parse(source, freeze)
                base_sha = (data.get("base_shas") or {}).get(alias) or gitops.merge_base(
                    source, base_ref
                )

                # Thin bundles need the base on the remote; a local-only base (no
                # `origin/...` ref) cannot be fetched elsewhere, so bundle it whole.
                thin = not full and base_ref.startswith("origin/")
                needed = (
                    any(_unique_commits(source, base_ref, r) for r in refs)
                    if thin
                    else all(gitops.rev_parse(source, r) for r in refs)
                )

                bundle_rel: str | None = None
                bundle_refs: list[str] = []
                if needed:
                    bundle_rel = f"{BUNDLES_DIR}/{alias}.bundle"
                    out = staging / bundle_rel
                    log(f"bundling {alias} ({freeze})...")
                    try:
                        gitops.bundle_create(
                            out, refs, source, exclude=base_ref if thin else None
                        )
                        bundle_refs = gitops.bundle_list_heads(out, source)
                    except gitops.GitError:
                        if out.exists():
                            out.unlink()
                        bundle_rel = None
                        bundle_refs = []

                repo_entries[alias] = {
                    "url": repo.url,
                    "default_base": repo.default_base,
                    "role": repo.role,
                    "test_branch": repo.test_branch,
                    "freeze_branch": freeze,
                    "base_ref": base_ref,
                    "base_sha": base_sha,
                    "head_sha": head_sha,
                    "thin": thin,
                    "bundle": bundle_rel,
                    "bundle_refs": bundle_refs,
                    "children": children,
                }

            accounts_rel: str | None = None
            if include_accounts:
                src_accounts = paths.req_accounts_yaml(root, jira)
                if src_accounts.is_file():
                    shutil.copy2(src_accounts, staging / ACCOUNTS_NAME)
                    accounts_rel = ACCOUNTS_NAME
                    log("including requirement accounts (plaintext)")

            manifest = {
                "format": EXPORT_FORMAT,
                "version": EXPORT_VERSION,
                "jira": jira,
                "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "phase": data.get("phase"),
                "branch": freeze,
                "accounts": accounts_rel,
                "repos": repo_entries,
            }
            atomic_write_text(
                staging / MANIFEST_NAME,
                yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
            )

            dest = dest.expanduser()
            if archive:
                if not dest.name.endswith((".tar.gz", ".tgz")):
                    dest = dest.with_name(dest.name + ".tar.gz")
                if dest.exists():
                    if not force:
                        raise FileExistsError(f"{dest} exists; pass --force to overwrite")
                    dest.unlink()
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tarfile.open(dest, "w:gz") as tar:
                    for child in sorted(staging.iterdir()):
                        tar.add(child, arcname=child.name)
                log(f"wrote archive {dest}")
                return dest

            if dest.exists():
                if not force:
                    raise FileExistsError(f"{dest} exists; pass --force to overwrite")
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(staging), str(dest))
            log(f"wrote bundle {dest}")
            return dest
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _strip_paths(status_file: Path) -> None:
    """Drop machine-specific worktree paths from the exported STATUS.yaml."""
    if not status_file.is_file():
        return
    try:
        data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return
    if not isinstance(data, dict):
        return
    tickets = st.tickets_map(data.get("tickets"))
    for slot in tickets.values():
        slot.pop("worktree", None)
        slot.pop("child_worktree", None)
    data["tickets"] = tickets
    atomic_write_text(
        status_file, yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    )


def _load_bundle_dir(src: Path) -> tuple[Path, Path | None]:
    """Return (bundle_root, extracted_tmpdir_or_None) for a dir or archive."""
    src = src.expanduser()
    if src.is_dir():
        root = src
    elif src.is_file():
        tmp = Path(tempfile.mkdtemp(prefix="dev-yard-import-"))
        try:
            with tarfile.open(src) as tar:
                tar.extractall(tmp, filter="data")
        except (tarfile.TarError, OSError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise BundleError(f"cannot read bundle archive {src}: {e}") from e
        # Prefer the shallowest manifest.yaml (the archive root) over any nested copy.
        matches = sorted(tmp.rglob(MANIFEST_NAME), key=lambda p: len(p.parts))
        if not matches:
            shutil.rmtree(tmp, ignore_errors=True)
            raise BundleError(f"{src} has no {MANIFEST_NAME}")
        return matches[0].parent, tmp
    else:
        raise FileNotFoundError(f"no bundle at {src}")
    if not (root / MANIFEST_NAME).is_file():
        raise BundleError(f"{root} has no {MANIFEST_NAME}")
    return root, None


def _read_manifest(bundle_root: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load((bundle_root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise BundleError(f"cannot read {MANIFEST_NAME}: {e}") from e
    if not isinstance(data, dict):
        raise BundleError(f"{MANIFEST_NAME} must be a mapping")
    if data.get("format") != EXPORT_FORMAT:
        raise BundleError(f"not a {EXPORT_FORMAT} bundle")
    version = data.get("version")
    if version != EXPORT_VERSION:
        raise BundleError(
            f"unsupported bundle version {version!r} (this build understands {EXPORT_VERSION})"
        )
    if not str(data.get("jira") or "").strip():
        raise BundleError("bundle manifest has no jira key")
    return data


def _ensure_repos(root: Path, manifest: dict[str, Any], log: Progress) -> None:
    """Register any missing repos from the manifest; verify URLs for the rest."""
    from dev_yard import service

    registered = load_repos(root)
    for alias, cfg in (manifest.get("repos") or {}).items():
        url = str(cfg.get("url") or "").strip()
        if not url:
            raise BundleError(f"repo {alias}: manifest has no url")
        existing = registered.get(alias)
        if existing is not None:
            if normalize_git_url(existing.url) != normalize_git_url(url):
                raise ValueError(
                    f"repo {alias}: local url {existing.url!r} differs from bundle {url!r}"
                )
            continue
        log(f"registering repo {alias} -> {url}")
        try:
            service.repo_add(
                root,
                alias,
                url,
                str(cfg.get("default_base") or "main"),
                str(cfg.get("role") or "svc"),
                None,
                on_progress=log,
                test_branch=cfg.get("test_branch"),
            )
        except gitops.GitError as e:
            # repo_add persists the entry before cloning; a failed clone (offline)
            # is fine when the bundle is self-contained — bootstrap it on import.
            log(f"warning: cannot clone {alias} ({e}); will rely on the bundle")


def _ensure_clone_or_init(root: Path, alias: str, url: str, source: Path, log: Progress) -> None:
    if not (source / ".git").exists():
        try:
            gitops.ensure_clone(url, source, on_progress=log)
        except gitops.GitError as e:
            log(f"warning: cannot clone {alias} ({e}); creating an empty repo for the bundle")
            gitops.init_repo(source)
        return
    try:
        gitops.fetch(source, on_progress=log)
    except gitops.GitError as e:
        log(f"warning: cannot fetch {alias} ({e}); importing from bundle only")


def _import_repo_branches(
    root: Path, bundle_root: Path, manifest: dict[str, Any], log: Progress
) -> None:
    """Fetch the bundled branches into each repo's clone."""
    repos = load_repos(root)
    for alias, cfg in (manifest.get("repos") or {}).items():
        repo = repos[alias]
        source = repo.source_path(root)
        _ensure_clone_or_init(root, alias, repo.url, source, log)

        bundle_rel = cfg.get("bundle")
        if not bundle_rel:
            continue
        bundle_path = bundle_root / _safe_rel(str(bundle_rel))
        if not bundle_path.is_file():
            raise BundleError(f"repo {alias}: bundle file {bundle_rel} is missing")
        refs = cfg.get("bundle_refs")
        if not refs:
            freeze = str(cfg.get("freeze_branch") or manifest.get("branch") or "").strip()
            refs = [freeze, *[str(b) for b in (cfg.get("children") or {}).values()]]
        refspecs = [
            f"+{r}:{r}" if r.startswith("refs/") else f"+refs/heads/{r}:refs/heads/{r}"
            for r in (str(x).strip() for x in refs)
            if r
        ]
        if not refspecs:
            continue
        log(f"importing branches for {alias}...")
        try:
            gitops.fetch_bundle(source, bundle_path, refspecs, on_progress=log)
        except gitops.GitError as e:
            hint = "" if cfg.get("thin") is False else (
                " (the bundle is thin: fetch the remote base, or re-export with --full)"
            )
            raise BundleError(f"repo {alias}: cannot import bundle{hint}: {e}") from e


def _recreate_worktrees(
    root: Path, jira: str, manifest: dict[str, Any], log: Progress
) -> None:
    repos = load_repos(root)
    for alias, cfg in (manifest.get("repos") or {}).items():
        repo = repos[alias]
        source = repo.source_path(root)
        freeze = str(cfg.get("freeze_branch") or manifest.get("branch") or "").strip()
        base_ref = str(cfg.get("base_ref") or "").strip() or repo.default_base
        start = freeze if gitops.rev_parse(source, freeze) else base_ref

        wt = paths.req_worktree(root, jira, alias)
        _reset_worktree(source, wt)
        log(f"recreating worktree {alias} ({freeze})...")
        gitops.worktree_add(source, wt, freeze, start, reset_existing=True)

        for tid, branch in (cfg.get("children") or {}).items():
            branch = str(branch)
            if gitops.rev_parse(source, branch) is None:
                continue
            child = paths.child_worktree(root, jira, alias, str(tid))
            _reset_worktree(source, child)
            log(f"recreating ticket worktree {alias}/{tid} ({branch})...")
            gitops.worktree_add(source, child, branch, branch, reset_existing=True)


def _reset_worktree(source: Path, path: Path) -> None:
    """Clear a leftover worktree/dir so `git worktree add` cannot reuse it wrongly."""
    if not path.exists():
        return
    if (path / ".git").exists():
        gitops.worktree_remove(source, path)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _rewrite_status_paths(
    root: Path, jira: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Point every ticket slot at this machine's worktree paths."""
    repo_cfg = manifest.get("repos") or {}
    with st.jira_lock(jira):
        data = st.load(root, jira)
        tickets = st.tickets_map(data.get("tickets"))
        for tid, slot in tickets.items():
            alias = str(slot.get("repo") or "")
            cfg = repo_cfg.get(alias)
            if cfg is None:
                slot.pop("worktree", None)
                slot.pop("child_worktree", None)
                continue
            slot["worktree"] = str(paths.req_worktree(root, jira, alias))
            children = cfg.get("children") or {}
            if tid in children:
                slot["child_worktree"] = str(
                    paths.child_worktree(root, jira, alias, tid)
                )
            else:
                slot.pop("child_worktree", None)
        data["tickets"] = tickets
        st.save(root, jira, data)
    return data


def req_import_bundle(
    root: Path,
    src: Path,
    *,
    force: bool = False,
    on_progress: Progress | None = None,
) -> dict[str, Any]:
    """Restore a bundle produced by `req_export` into this workspace.

    Registers missing repos, fetches the bundled branches, recreates the freeze
    and in-progress ticket worktrees, and restores the requirement docs and
    STATUS.yaml. Refuses to overwrite an existing requirement unless `force`.
    """
    from dev_yard import service

    def log(line: str) -> None:
        if on_progress:
            on_progress(line)

    bundle_root, tmp = _load_bundle_dir(src)
    try:
        manifest = _read_manifest(bundle_root)
        jira = str(manifest["jira"]).strip()
        req = paths.req_dir(root, jira)

        if req.exists():
            if not force:
                raise ValueError(
                    f"{jira} already exists; pass --force to overwrite it"
                )
            log(f"removing existing requirement {jira}...")
            if paths.is_req_dir(req):
                service.req_delete(root, jira)
            else:
                shutil.rmtree(req)

        docs_src = bundle_root / DOCS_TOP / jira
        if not docs_src.is_dir():
            raise BundleError(f"bundle is missing {DOCS_TOP}/{jira}/")

        _ensure_repos(root, manifest, log)
        _import_repo_branches(root, bundle_root, manifest, log)
        _copy_tree(docs_src, req)
        _recreate_worktrees(root, jira, manifest, log)

        accounts_rel = manifest.get("accounts")
        if accounts_rel:
            accounts_src = bundle_root / _safe_rel(str(accounts_rel))
            if accounts_src.is_file():
                dest = paths.req_accounts_yaml(root, jira)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(accounts_src, dest)
                log("restored requirement accounts")

        data = _rewrite_status_paths(root, jira, manifest)
        log(f"restored {jira} phase={data.get('phase')}")
        return data
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
