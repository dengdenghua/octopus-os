"""Guard health CLI — view precision, diagnose false positives.

Usage::

    echo guard-health                    # Show overall health
    echo guard-health --top 10           # Top 10 by hits
    echo guard-health --noisy            # Show low-precision guards
    echo guard-health --unjudged         # Show unjudged high-frequency guards
    echo guard-health --recommend        # Recommend actions

Examples::

    # Daily operator check
    echo guard-health --noisy

    # Before tuning session
    echo guard-health --recommend

    # After running judge batch
    echo guard-health --unjudged
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from runtime.safety.evolution.guard_telemetry import GuardTelemetry


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="guard-health",
        description="View guard precision and diagnose false positives.",
    )
    parser.add_argument(
        "--telemetry",
        type=Path,
        default=None,
        help="Path to guard telemetry file (default: auto-detect).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Show top N guards by hit count.",
    )
    parser.add_argument(
        "--noisy",
        action="store_true",
        help="Show only guards with precision < 50%% (likely false positives).",
    )
    parser.add_argument(
        "--unjudged",
        action="store_true",
        help="Show high-frequency guards with no verdicts yet.",
    )
    parser.add_argument(
        "--recommend",
        action="store_true",
        help="Recommend actions (disable noisy guards, run judge on unjudged).",
    )
    parser.add_argument(
        "--min-precision",
        type=float,
        default=0.5,
        help="Precision threshold for tuning candidates (default 0.5).",
    )
    parser.add_argument(
        "--tuning-threshold",
        type=int,
        default=20,
        help="Min hits for tuning candidates (default 20).",
    )
    return parser.parse_args(argv)


def _render_health(
    digest: dict,
    *,
    show_top: int | None = None,
    show_noisy: bool = False,
    show_unjudged: bool = False,
    show_recommend: bool = False,
) -> str:
    """Render guard health report."""
    lines = []
    total = digest["total_hits"]
    judged = digest["judged_total"]

    # Header
    lines.append("=" * 80)
    lines.append("GUARD HEALTH REPORT")
    lines.append("=" * 80)
    lines.append(f"Total hits: {total}")
    lines.append(f"Judged: {judged} ({judged / total * 100:.1f}%)" if total else "Judged: 0")
    lines.append(
        f"Unjudged: {total - judged} ({(total - judged) / total * 100:.1f}%)" if total else ""
    )
    lines.append("")

    label_precision = digest["label_precision"]

    # Noisy guards (low precision)
    noisy_guards = [
        (label, data)
        for label, data in label_precision.items()
        if data["precision"] is not None and data["precision"] < 0.5
    ]
    if show_noisy or show_recommend:
        lines.append("NOISY GUARDS (precision < 50%)")
        lines.append("-" * 80)
        if noisy_guards:
            for label, data in sorted(noisy_guards, key=lambda x: x[1]["precision"]):
                prec = data["precision"]
                tp = data["tp"]
                fp = data["fp"]
                lines.append(f"  {label:40s} precision={prec:.2%} (tp={tp}, fp={fp})")
        else:
            lines.append("  ✓ No noisy guards found")
        lines.append("")

    # Unjudged high-frequency guards
    unjudged_candidates = [
        (label, data)
        for label, data in label_precision.items()
        if data["precision"] is None and data["unjudged"] >= 20
    ]
    if show_unjudged or show_recommend:
        lines.append("UNJUDGED HIGH-FREQUENCY GUARDS (≥20 hits, no verdicts)")
        lines.append("-" * 80)
        if unjudged_candidates:
            for label, data in sorted(unjudged_candidates, key=lambda x: -x[1]["unjudged"]):
                unjudged = data["unjudged"]
                lines.append(f"  {label:40s} {unjudged} unjudged hits")
        else:
            lines.append("  ✓ All high-frequency guards are judged")
        lines.append("")

    # Top N by hits
    if show_top:
        lines.append(f"TOP {show_top} GUARDS BY HITS")
        lines.append("-" * 80)
        by_label = digest["by_label"]
        for i, (label, count) in enumerate(
            sorted(by_label.items(), key=lambda x: -x[1])[:show_top], 1
        ):
            data = label_precision[label]
            prec = data["precision"]
            prec_str = f"{prec:.2%}" if prec is not None else "no verdicts"
            lines.append(f"  {i:2d}. {label:40s} {count:4d} hits, precision={prec_str}")
        lines.append("")

    # Recommendations
    if show_recommend:
        lines.append("RECOMMENDED ACTIONS")
        lines.append("-" * 80)
        actions = []

        if noisy_guards:
            noisy_labels = ", ".join(f'"{label}"' for label, _ in noisy_guards[:3])
            if len(noisy_guards) > 3:
                noisy_labels += f", ... ({len(noisy_guards) - 3} more)"
            actions.append(
                f"1. Disable {len(noisy_guards)} noisy guards:\n"
                f'   export ECHO_DISABLED_GUARDS="{noisy_labels}"'
            )

        if unjudged_candidates:
            total_unjudged = sum(data["unjudged"] for _, data in unjudged_candidates)
            actions.append(
                f"2. Run judge on {total_unjudged} unjudged hits:\n"
                f"   python -m runtime.safety.evolution.run_batch_cron --max-hits {min(total_unjudged, 100)}"
            )

        if not actions:
            lines.append("  ✓ No actions needed — guards are healthy")
        else:
            for action in actions:
                lines.append(f"  {action}")
                lines.append("")

    # Default view (summary)
    if not (show_top or show_noisy or show_unjudged or show_recommend):
        lines.append("SUMMARY BY CATEGORY")
        lines.append("-" * 80)
        by_category = digest["by_category"]
        for category, count in by_category.items():
            share = digest["category_share"][category]
            lines.append(f"  {category:20s} {count:4d} hits ({share:.1%})")
        lines.append("")

        lines.append("TUNING CANDIDATES (high-frequency, not noisy)")
        lines.append("-" * 80)
        candidates = digest["tuning_candidates"]
        if candidates:
            for cand in candidates[:10]:
                label = cand["label"]
                count = cand["count"]
                prec = cand["precision"]
                prec_str = f"{prec:.2%}" if prec is not None else "not judged"
                lines.append(f"  {label:40s} {count:4d} hits, precision={prec_str}")
            if len(candidates) > 10:
                lines.append(f"  ... and {len(candidates) - 10} more")
        else:
            lines.append("  (none — adjust --tuning-threshold or run judge first)")
        lines.append("")

        lines.append("TIP: Run with --recommend to see actionable next steps")

    lines.append("=" * 80)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Auto-detect telemetry path
    telemetry_path = args.telemetry
    if telemetry_path is None:
        # Default location
        from runtime.core.cerebrum.react_loop_controls import _guard_hit_recorder

        _ = _guard_hit_recorder()  # Initialize singleton
        from runtime.core.cerebrum.react_loop_controls import _GUARD_TELEMETRY_SINGLETON

        if _GUARD_TELEMETRY_SINGLETON is None:
            print("ERROR: Guard telemetry not available", file=sys.stderr)
            return 1
        telemetry_path = _GUARD_TELEMETRY_SINGLETON._path

    telemetry = GuardTelemetry(path=telemetry_path)

    digest = telemetry.digest(
        tuning_threshold=args.tuning_threshold,
        min_precision_for_tuning=args.min_precision,
    )

    if digest["total_hits"] == 0:
        print("No guard hits recorded yet.")
        return 0

    report = _render_health(
        digest,
        show_top=args.top,
        show_noisy=args.noisy,
        show_unjudged=args.unjudged,
        show_recommend=args.recommend,
    )
    print(report)
    return 0


def run_guard_health(
    telemetry_path: str | Path | None = None,
    top: int | None = None,
    noisy: bool = False,
    unjudged: bool = False,
    recommend: bool = False,
    min_precision: float = 0.5,
    tuning_threshold: int = 20,
) -> int:
    """Entrypoint for echo-agent CLI dispatcher."""
    # Build argv from parameters
    argv = []
    if telemetry_path:
        argv.extend(["--telemetry", str(telemetry_path)])
    if top is not None:
        argv.extend(["--top", str(top)])
    if noisy:
        argv.append("--noisy")
    if unjudged:
        argv.append("--unjudged")
    if recommend:
        argv.append("--recommend")
    if min_precision != 0.5:
        argv.extend(["--min-precision", str(min_precision)])
    if tuning_threshold != 20:
        argv.extend(["--tuning-threshold", str(tuning_threshold)])

    return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
