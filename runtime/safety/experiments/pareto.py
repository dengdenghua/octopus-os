from __future__ import annotations

from collections.abc import Mapping


def pareto_frontier_by_name(
    points: Mapping[str, Mapping[str, float]],
    metrics: tuple[str, ...],
    *,
    maximize: Mapping[str, bool],
) -> set[str]:
    valid = {name: pt for name, pt in points.items() if all(m in pt for m in metrics)}
    if not valid:
        return set()
    if len(valid) == 1:
        return set(valid.keys())

    sign = [1.0 if maximize.get(m, True) else -1.0 for m in metrics]
    vecs = {
        name: tuple(sign[i] * pt[m] for i, m in enumerate(metrics)) for name, pt in valid.items()
    }

    frontier: set[str] = set(valid.keys())
    for a_name, a_vec in vecs.items():
        for b_name, b_vec in vecs.items():
            if a_name == b_name:
                continue
            if _dominates(b_vec, a_vec):
                frontier.discard(a_name)
                break
    return frontier


def _dominates(x: tuple[float, ...], y: tuple[float, ...]) -> bool:
    if len(x) != len(y):
        return False
    all_ge = True
    any_gt = False
    for xi, yi in zip(x, y, strict=False):
        if xi < yi:
            all_ge = False
            break
        if xi > yi:
            any_gt = True
    return all_ge and any_gt
