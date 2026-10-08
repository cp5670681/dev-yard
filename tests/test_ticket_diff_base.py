"""Ticket diff base when a ticket's earlier work already landed on the freeze branch.

A re-implement syncs the parent tip into the ticket's child worktree. After that
`merge_base(child, parent_head)` *is* the parent head, so the fold in
`_ticket_base_sha` is the only thing standing between the reviewer and a diff
that shows just the ticket's last commit.

The fold replays every sibling commit onto a tree without the ticket. A sibling
that edited the same file afterwards carries the ticket's version in its patch
context, so the replay used to give up and fall back to the live parent tip.
"""

from pathlib import Path

from dev_yard import gitops, service
from dev_yard import status as st
from dev_yard.service import init_yard, repo_add, req_open, req_freeze

FILE = "app/list.rb"
SEED = "1\n2\n3\nb\n5\n6\n7\n8\n9\n10\n"
OWN = "1\n2\n3\nB1\n5\n6\n7\n8\n9\n10\n"
# Three lines below the ticket's line: the sibling's hunk context still spells
# out the ticket's version, so a plain `apply` cannot replay it.
SIBLING = "1\n2\n3\nB1\n5\n6\nS7\n8\n9\n10\n"
FOLDED = "1\n2\n3\nb\n5\n6\nS7\n8\n9\n10"  # ticket's line undone, sibling's kept


def _write(path: Path, text: str, message: str) -> None:
    target = path / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    gitops.run(["git", "add", "-A"], cwd=path)
    gitops.run(["git", "commit", "-q", "-m", message], cwd=path)


def _committed(path: Path, ref: str, name: str = FILE) -> str:
    return gitops.run(["git", "show", f"{ref}:{name}"], cwd=path)


def _changed_lines(diff: str) -> list[str]:
    """Added/removed lines only; context lines are not the ticket's doing."""
    return [
        line
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def test_fold_keeps_a_sibling_that_touches_the_same_file(tmp_path: Path, git_src: Path):
    """The fold must not bail out when a later sibling edited the same file."""
    _write(git_src, SEED, "seed list")
    frozen = gitops.head_sha(git_src)

    freeze_branch = "tom/AB-90"
    gitops.run(["git", "checkout", "-q", "-b", freeze_branch], cwd=git_src)
    child = tmp_path / "child"
    ticket_branch = f"{freeze_branch}-T1"
    gitops.worktree_add(git_src, child, ticket_branch, freeze_branch)

    _write(child, OWN, "feat(T1): own work")
    gitops.merge_into(git_src, ticket_branch)
    # Sibling lands after the merge and edits a neighbouring line, so its patch
    # context still contains the ticket's own line.
    _write(git_src, SIBLING, "feat(T2): sibling work")
    tip = gitops.head_sha(git_src)
    folded = gitops.baseline_without_ticket(
        git_src, tip, ticket_branch, "T1", not_before=frozen
    )

    assert folded is not None, "fold gave up"
    assert folded != tip
    assert _committed(git_src, folded) == FOLDED


def test_ticket_base_folds_own_merged_work_after_parent_sync(
    tmp_path: Path, git_src: Path, monkeypatch
):
    """The diff base is the code before this ticket, not the live parent tip."""
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    _write(git_src, SEED, "seed list")

    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    key = "AB-91"
    req_open(yard, key, source="none")
    req = yard / "reqs" / key
    (req / "TICKETS.md").write_text(
        "## T1: a\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: b\n- repo: backend\n- depends_on: T1\n- parallel: false\n"
    )
    req_freeze(yard, key)

    data = st.load(yard, key)
    freeze_branch = data["branch"]
    parent = Path(data["tickets"]["T1"]["worktree"])
    child = tmp_path / "child-T1"
    ticket_branch = f"{freeze_branch}-T1"
    gitops.worktree_add(git_src, child, ticket_branch, freeze_branch)

    _write(child, OWN, "feat(T1): own work")
    gitops.merge_into(parent, ticket_branch)
    _write(parent, SIBLING, "feat(T2): sibling work")
    # Re-implement syncs the parent tip into the child, as `merge(<T1>)` does.
    gitops.merge_into(child, freeze_branch)

    parent_head = gitops.head_sha(parent)
    assert gitops.merge_base(child, parent_head) == parent_head
    slot = {
        "state": "approved",
        "worktree": str(parent),
        "child_worktree": str(child),
    }
    data["tickets"]["T1"].update(slot)

    base = service._ticket_base_sha(data, [], "T1", "backend", slot, child)

    assert base is not None
    assert base != parent_head, "flew back to the live parent tip"
    changed = _changed_lines(gitops.run(["git", "diff", base], cwd=child))
    assert any("B1" in line for line in changed), "the ticket's own change is missing"
    assert not any("S7" in line for line in changed), "the sibling's change leaked in"

def test_fold_handles_sibling_file_deletion_and_rename(tmp_path: Path, git_src: Path):
    """The fold handles sibling commits that delete or rename files alongside 3-way edits."""
    _write(git_src, SEED, "seed list")
    del_file = git_src / "to_delete.txt"
    del_file.write_text("will be deleted", encoding="utf-8")
    rename_file = git_src / "old_name.txt"
    rename_file.write_text("old content", encoding="utf-8")
    gitops.run(["git", "add", "-A"], cwd=git_src)
    gitops.run(["git", "commit", "-q", "-m", "add extra files"], cwd=git_src)
    frozen = gitops.head_sha(git_src)

    freeze_branch = "tom/AB-92"
    gitops.run(["git", "checkout", "-q", "-b", freeze_branch], cwd=git_src)
    child = tmp_path / "child"
    ticket_branch = f"{freeze_branch}-T1"
    gitops.worktree_add(git_src, child, ticket_branch, freeze_branch)

    _write(child, OWN, "feat(T1): own work")
    gitops.merge_into(git_src, ticket_branch)

    # Sibling 1: modifies list.rb (with T1 line in context) and deletes to_delete.txt
    target = git_src / FILE
    target.write_text(SIBLING, encoding="utf-8")
    (git_src / "to_delete.txt").unlink()
    gitops.run(["git", "add", "-A"], cwd=git_src)
    gitops.run(["git", "commit", "-q", "-m", "feat(T2): sibling delete and edit"], cwd=git_src)

    # Sibling 2: renames old_name.txt to new_name.txt
    (git_src / "old_name.txt").unlink()
    (git_src / "new_name.txt").write_text("renamed content", encoding="utf-8")
    gitops.run(["git", "add", "-A"], cwd=git_src)
    gitops.run(["git", "commit", "-q", "-m", "feat(T3): sibling rename"], cwd=git_src)

    tip = gitops.head_sha(git_src)
    folded = gitops.baseline_without_ticket(
        git_src, tip, ticket_branch, "T1", not_before=frozen
    )

    assert folded is not None, "fold gave up on deletion/rename"
    assert folded != tip
    assert _committed(git_src, folded) == FOLDED
    # to_delete.txt must not exist in folded baseline
    try:
        gitops.run(["git", "show", f"{folded}:to_delete.txt"], cwd=git_src)
        deleted_still_present = True
    except gitops.GitError:
        deleted_still_present = False
    assert not deleted_still_present, "to_delete.txt was not deleted in baseline"
    assert _committed(git_src, folded, "new_name.txt") == "renamed content"
