"""Tournament selection — pure best-of-N picker + the wired skill handler."""

from __future__ import annotations

from runtime.execution.subagents import worktree_loop as wl
from runtime.execution.subagents.tournament import Candidate, select_winner
from runtime.execution.suckers import delegation_skills as ds

# ── pure primitive: select_winner ───────────────────────────────────


def _c(cid, output, ok=True):
    return Candidate(id=cid, output=output, ok=ok, meta={"files": [f"{cid}.py"]})


def test_no_viable_candidate_returns_none() -> None:
    res = select_winner([_c("a", "", ok=True), _c("b", "x", ok=False)], judge=lambda v: "a")
    assert res.winner is None
    assert res.decided_by == "none"
    assert res.viable_count == 0


def test_single_viable_wins_unjudged() -> None:
    called = {"n": 0}

    def judge(_v):
        called["n"] += 1
        return "a"

    res = select_winner([_c("a", "diff", ok=True), _c("b", "", ok=False)], judge=judge)
    assert res.winner.id == "a"
    assert res.decided_by == "only_candidate"
    assert called["n"] == 0  # judge not consulted for a single viable candidate


def test_judge_picks_winner_and_runners_up() -> None:
    cands = [_c("a", "dA"), _c("b", "dB"), _c("c", "dC")]
    res = select_winner(cands, judge=lambda v: "b")
    assert res.winner.id == "b"
    assert res.decided_by == "judge"
    assert [c.id for c in res.runners_up] == ["a", "c"]


def test_judge_abstain_or_junk_falls_back_to_first_viable() -> None:
    cands = [_c("a", "dA"), _c("b", "dB")]
    assert select_winner(cands, judge=lambda v: None).winner.id == "a"
    res = select_winner(cands, judge=lambda v: "nonexistent")
    assert res.winner.id == "a"
    assert res.decided_by == "judge_abstained"


# ── wired skill handler ──────────────────────────────────────────────


def _loop_with(results):
    return lambda root, tasks, worker, **kw: {"ok": True, "results": results}


def test_handler_missing_goal_errors() -> None:
    r = ds._run_tournament(goal="")
    assert r["ok"] is False
    assert "required" in r["error"]


def test_handler_not_a_git_repo_errors(monkeypatch) -> None:
    monkeypatch.setattr(wl, "is_git_repo", lambda _root: False)
    r = ds._run_tournament(goal="do X", repo_root="/not/a/repo")
    assert r["ok"] is False
    assert "not a git repo" in r["error"]


def test_handler_runs_candidates_and_judge_picks_winner(monkeypatch) -> None:
    monkeypatch.setattr(wl, "is_git_repo", lambda _root: True)
    monkeypatch.setattr(wl, "subagent_worktree_worker", lambda **kw: lambda p, t: None)
    monkeypatch.setattr(
        wl,
        "run_worktree_loop",
        _loop_with(
            [
                {"index": 0, "ok": True, "diff": "diff A", "files": ["a.py"], "branch": "b0"},
                {"index": 1, "ok": True, "diff": "diff B", "files": ["b.py"], "branch": "b1"},
                {"index": 2, "ok": True, "diff": "diff C", "files": ["c.py"], "branch": "b2"},
            ]
        ),
    )
    seen = {}

    def fake_vote(*, question, n, choices, **_kw):
        seen["choices"] = choices
        seen["question"] = question
        return {"ok": True, "verdict": "candidate_2", "confidence": 0.8, "votes": []}

    monkeypatch.setattr(ds, "_call_agent_vote", fake_vote)

    r = ds._run_tournament(goal="implement X", n=3, repo_root="/repo")
    assert r["ok"] is True
    assert r["decided_by"] == "judge"
    assert r["winner"]["id"] == "candidate_2"
    assert r["winner"]["diff"] == "diff B"
    assert r["candidate_count"] == 3
    assert r["viable_count"] == 3
    # the judge was offered exactly the candidate ids as the ballot
    assert seen["choices"] == ["candidate_1", "candidate_2", "candidate_3"]
    assert "implement X" in seen["question"]


def test_handler_all_candidates_failed_returns_no_winner(monkeypatch) -> None:
    monkeypatch.setattr(wl, "is_git_repo", lambda _root: True)
    monkeypatch.setattr(wl, "subagent_worktree_worker", lambda **kw: lambda p, t: None)
    monkeypatch.setattr(
        wl,
        "run_worktree_loop",
        _loop_with(
            [
                {"index": 0, "ok": False, "diff": "", "files": [], "error": "boom"},
                {"index": 1, "ok": False, "diff": "", "files": [], "error": "boom"},
            ]
        ),
    )

    def fake_vote(**_kw):  # must NOT be consulted when nothing is viable
        raise AssertionError("judge called with no viable candidates")

    monkeypatch.setattr(ds, "_call_agent_vote", fake_vote)

    r = ds._run_tournament(goal="implement X", n=2, repo_root="/repo")
    assert r["ok"] is False
    assert r["decided_by"] == "none"
    assert r["winner"] is None
    assert r["viable_count"] == 0

