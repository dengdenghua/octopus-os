"""Tournament selection over isolated candidates — pick the best of N attempts.

``worktree_loop`` already runs N tasks, each in its own git worktree, and
returns their diffs — but it deliberately never picks a winner ("reconciling
parallel edits is a human decision"). A *tournament* is the missing other half:
run the SAME goal N times in isolation, then have an independent judge choose
the best result.

This module is the pure selection logic — producer-agnostic and judge-agnostic
— so it is unit-testable without git or an LLM. Like ``worktree_loop`` it never
auto-applies anything: the winner's diff is returned for a human/lead to review.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Candidate:
    id: str
    output: str  # the diff (or answer) this candidate produced
    ok: bool = True
    meta: Mapping[str, object] = field(default_factory=dict)

    @property
    def viable(self) -> bool:
        """A candidate worth judging: it ran AND produced something."""
        return self.ok and bool((self.output or "").strip())


# judge(viable_candidates) -> winning candidate id, or None to abstain.
Judge = Callable[[list["Candidate"]], "str | None"]


@dataclass(frozen=True)
class TournamentResult:
    winner: Candidate | None
    candidates: list[Candidate]
    decided_by: str  # "judge" | "only_candidate" | "judge_abstained" | "none"
    runners_up: list[Candidate] = field(default_factory=list)

    @property
    def viable_count(self) -> int:
        return sum(1 for c in self.candidates if c.viable)


def select_winner(candidates: list[Candidate], judge: Judge) -> TournamentResult:
    """Pick the best viable candidate.

    Degrades gracefully, never raises:
      * no viable candidate            → ``winner=None``, decided_by="none"
      * exactly one viable             → it wins unjudged ("only_candidate")
      * judge names a real id          → that one wins ("judge")
      * judge abstains / names junk    → deterministic fallback to the first
                                         viable candidate ("judge_abstained")
    """
    viable = [c for c in candidates if c.viable]
    if not viable:
        return TournamentResult(None, candidates, "none")
    if len(viable) == 1:
        return TournamentResult(viable[0], candidates, "only_candidate")

    winner_id = judge(viable)
    chosen = next((c for c in viable if c.id == winner_id), None)
    if chosen is None:
        return TournamentResult(viable[0], candidates, "judge_abstained", viable[1:])
    return TournamentResult(
        chosen,
        candidates,
        "judge",
        [c for c in viable if c.id != chosen.id],
    )
