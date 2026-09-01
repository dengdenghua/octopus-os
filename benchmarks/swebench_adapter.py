"""SWE-bench adapter — generates predictions for SWE-bench Verified / Lite.

This adapter bridges SWE-bench instances to the Echo Agent runtime.
For each instance it:

1. Clones the target repo at ``base_commit`` into an isolated workspace.
2. Invokes ``echo-agent code`` (headless CLI) with the problem statement.
3. Extracts the agent's patch via ``git diff``.
4. Writes ``predictions.jsonl`` in the official SWE-bench format.

Usage::

    # Generate predictions on SWE-bench Lite (10 samples for smoke test)
    python -m benchmarks.swebench_adapter \\
        --dataset princeton-nlp/SWE-bench_Lite \\
        --max-samples 10 \\
        --output predictions.jsonl

    # Full run on SWE-bench Verified
    python -m benchmarks.swebench_adapter \\
        --dataset princeton-nlp/SWE-bench_Verified \\
        --output predictions.jsonl \\
        --max-workers 4

After generating predictions, evaluate with the official harness::

    python -m swebench.harness.run_evaluation \\
        --dataset_name princeton-nlp/SWE-bench_Verified \\
        --predictions_path predictions.jsonl \\
        --max_workers 16 \\
        --run_id echo_v020
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("swebench_adapter")

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── Defaults ────────────────────────────────────────────────

DEFAULT_DATASET = "princeton-nlp/SWE-bench_Verified"
DEFAULT_WORKSPACE_ROOT = REPO_ROOT / "benchmarks" / "results" / "swebench"
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_MAX_USD = 5.0
DEFAULT_TIMEOUT = 1800.0  # 30 minutes per instance
DEFAULT_MODEL = "claude-sonnet-4-20250514"

SWE_BENCH_PROMPT_TEMPLATE = """\
You are a software engineer tasked with resolving a GitHub issue.

## Issue
{problem_statement}

## Instructions
1. Explore the repository structure to understand the codebase.
2. Search for relevant code using grep, code_search, or ast_search.
3. Identify the root cause of the issue described above.
4. **Make the minimal changes needed to fix the issue.** You MUST actually modify the source code with a file-write tool — do NOT just describe or plan the change. A turn that ends without a real file-write is a failure.
5. Run the relevant tests to verify your fix works.
6. Tests must remain unchanged; limit changes to source code only.
7. Ensure your changes are committed (staged) so they appear in `git diff`.

Remember:
- Keep changes minimal and focused on the issue.
- Match the existing code style of the project.
- If tests fail, iterate on your fix until they pass.
- **Action over description**: every iteration must either call a tool or finish. Never end an iteration with only a plan/read — apply the fix as soon as you know what to change.
"""


# ── Data models ─────────────────────────────────────────────


@dataclass
class SwebenchInstance:
    """A single SWE-bench task instance."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    test_patch: str = ""
    version: str = ""

    @classmethod
    def from_dataset_row(cls, row: dict[str, Any]) -> SwebenchInstance:
        fail_to_pass = row.get("FAIL_TO_PASS")
        if isinstance(fail_to_pass, str):
            fail_to_pass = json.loads(fail_to_pass)
        pass_to_pass = row.get("PASS_TO_PASS")
        if isinstance(pass_to_pass, str):
            pass_to_pass = json.loads(pass_to_pass)
        return cls(
            instance_id=str(row["instance_id"]),
            repo=str(row["repo"]),
            base_commit=str(row["base_commit"]),
            problem_statement=str(row.get("problem_statement", "")),
            hints_text=str(row.get("hints_text", "")),
            fail_to_pass=list(fail_to_pass or []),
            pass_to_pass=list(pass_to_pass or []),
            test_patch=str(row.get("test_patch", "")),
            version=str(row.get("version", "")),
        )

    @property
    def repo_slug(self) -> str:
        """Convert ``owner/name`` to ``owner__name`` for directory naming."""
        return self.repo.replace("/", "__")


@dataclass
class SwebenchPrediction:
    """A single prediction entry in predictions.jsonl."""

    instance_id: str
    model: str
    prediction: str  # unified diff patch

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "instance_id": self.instance_id,
                "model": self.model,
                "prediction": self.prediction,
            },
            ensure_ascii=False,
        )


@dataclass
class InstanceResult:
    """Result of processing a single SWE-bench instance."""

    instance_id: str
    prediction: SwebenchPrediction | None = None
    error: str | None = None
    duration_seconds: float = 0.0
    workspace: str = ""
    event_count: int = 0
    patch_lines: int = 0


# ── Dataset loading ─────────────────────────────────────────


def load_instances(
    dataset_name: str,
    *,
    max_samples: int | None = None,
    instance_ids: list[str] | None = None,
) -> list[SwebenchInstance]:
    """Load SWE-bench instances from HuggingFace datasets.

    Requires the ``datasets`` package: ``pip install datasets``.
    """
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise ImportError(
            "The 'datasets' package is required. Install with: pip install datasets"
        ) from e

    logger.info("Loading dataset %s ...", dataset_name)
    ds = load_dataset(dataset_name, split="test")

    rows: list[dict[str, Any]] = list(ds)
    if instance_ids:
        id_set = set(instance_ids)
        rows = [r for r in rows if r["instance_id"] in id_set]
    if max_samples and max_samples > 0:
        rows = rows[:max_samples]

    instances = [SwebenchInstance.from_dataset_row(r) for r in rows]
    logger.info("Loaded %d instances from %s", len(instances), dataset_name)
    return instances


# ── Workspace preparation ───────────────────────────────────


def prepare_workspace(
    instance: SwebenchInstance,
    workspace_root: Path,
    *,
    repos_cache: Path | None = None,
) -> Path:
    """Clone the repo and checkout ``base_commit`` for the given instance.

    Returns the path to the prepared workspace directory.
    Uses a shared clone cache to avoid re-cloning on every instance.
    """
    workspace = workspace_root / instance.repo_slug / instance.instance_id
    workspace.mkdir(parents=True, exist_ok=True)

    cache_dir = repos_cache or (workspace_root / "_repo_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_clone = cache_dir / instance.repo_slug

    # Clone or update the cache
    if not cached_clone.exists():
        clone_url = f"https://github.com/{instance.repo}.git"
        logger.info("[%s] Cloning %s ...", instance.instance_id, instance.repo)
        _run_git(
            cached_clone.parent,
            ["clone", "--quiet", clone_url, str(cached_clone)],
            timeout_s=300,
        )
    else:
        # Update existing cache
        _run_git(cached_clone, ["fetch", "--all", "--quiet"], timeout_s=120)

    # Copy or shallow clone from cache to workspace
    if not (workspace / ".git").exists():
        _run_git(
            workspace.parent,
            [
                "clone",
                "--quiet",
                "--no-local",
                str(cached_clone),
                str(workspace),
            ],
            timeout_s=120,
        )

    # Checkout the exact base commit
    _run_git(workspace, ["checkout", "--quiet", instance.base_commit], timeout_s=30)
    # Clean any leftover changes
    _run_git(workspace, ["reset", "--hard", "--quiet"], timeout_s=15)
    _run_git(workspace, ["clean", "-fdq"], timeout_s=15)

    logger.info(
        "[%s] Workspace ready at %s @ %s",
        instance.instance_id,
        workspace,
        instance.base_commit[:8],
    )
    return workspace


# ── Agent invocation ────────────────────────────────────────


def _build_agent_env(model: str) -> dict[str, str]:
    """Build env vars for the agent subprocess from custom_models.json.

    When the model is configured with an OpenAI-compatible ``base_url``,
    inject ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` so ``_make_router``
    picks the OpenAI router instead of falling back to Anthropic.
    """
    env = {**os.environ, "NO_COLOR": "1"}
    custom_models_path = REPO_ROOT / "data" / "custom_models.json"
    if not custom_models_path.is_file():
        return env
    try:
        with open(custom_models_path, encoding="utf-8") as f:
            custom_models = json.load(f)
    except (json.JSONDecodeError, OSError):
        return env
    cfg = custom_models.get(model)
    if not cfg or cfg.get("provider") != "openai":
        return env
    base_url = cfg.get("base_url")
    api_key = cfg.get("api_key")
    if base_url:
        env["OPENAI_BASE_URL"] = base_url
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    return env


def run_echo_agent(
    instance: SwebenchInstance,
    workspace: Path,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_usd: float = DEFAULT_MAX_USD,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Invoke ``echo-agent code`` on a single instance.

    Returns a dict with ``patch``, ``events``, ``success``, ``error``.
    """
    prompt = SWE_BENCH_PROMPT_TEMPLATE.format(
        problem_statement=instance.problem_statement,
    )
    if instance.hints_text.strip():
        prompt += f"\n## Hints\n{instance.hints_text}\n"

    command = [
        sys.executable,
        "-m",
        "runtime.cli",
        "code",
        "--cwd",
        str(workspace),
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "json",
        "--max-iterations",
        str(max_iterations),
        "--max-usd",
        str(max_usd),
    ]
    if model:
        command.extend(["--model", model])
    if extra_args:
        command.extend(extra_args)
    command.append(prompt)

    agent_env = _build_agent_env(model)

    logger.info(
        "[%s] Starting agent (model=%s, timeout=%ss)", instance.instance_id, model, timeout_seconds
    )
    start = time.time()

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            cwd=str(REPO_ROOT),
            env=agent_env,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        logger.warning("[%s] Timed out after %.0fs", instance.instance_id, elapsed)
        return {
            "patch": "",
            "events": [],
            "success": False,
            "error": f"timeout after {elapsed:.0f}s",
        }

    elapsed = time.time() - start

    # Parse the JSON output
    output: dict[str, Any] = {}
    if completed.stdout.strip():
        try:
            output = json.loads(completed.stdout)
        except json.JSONDecodeError:
            # Try to find the last JSON line
            for line in reversed(completed.stdout.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        output = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue

    success = output.get("success", completed.returncode == 0)
    final_answer = output.get("final_answer", "")
    event_count = output.get("event_count", 0)

    # Extract the patch via git diff in the workspace
    patch = extract_patch(workspace)

    logger.info(
        "[%s] Finished in %.1fs: success=%s, patch_lines=%d, events=%d",
        instance.instance_id,
        elapsed,
        success,
        len(patch.splitlines()),
        event_count,
    )
    if not success and not patch and completed.stderr.strip():
        logger.error("[%s] Agent stderr:\n%s", instance.instance_id, completed.stderr[-3000:])

    return {
        "patch": patch,
        "events": [],
        "success": success,
        "error": completed.stderr[-2000:] if not success and not patch else None,
        "duration": elapsed,
        "event_count": event_count,
        "final_answer": final_answer,
    }


def extract_patch(workspace: Path) -> str:
    """Extract the unified diff of changes made in the workspace."""
    # Stage all changes (including untracked) to capture them in diff
    _run_git(workspace, ["add", "-A"], timeout_s=15)
    result = _run_git(workspace, ["diff", "--cached"], timeout_s=30)
    patch = result.get("stdout", "")
    # Reset staging to not interfere with SWE-bench evaluation
    _run_git(workspace, ["reset", "--quiet"], timeout_s=15)
    return patch


# ── Single instance pipeline ────────────────────────────────


def process_instance(
    instance: SwebenchInstance,
    workspace_root: Path,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_usd: float = DEFAULT_MAX_USD,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    repos_cache: Path | None = None,
    extra_args: list[str] | None = None,
) -> InstanceResult:
    """Process a single SWE-bench instance end-to-end.

    Returns an :class:`InstanceResult` with the prediction or error.
    """
    start = time.time()
    try:
        workspace = prepare_workspace(instance, workspace_root, repos_cache=repos_cache)
        agent_result = run_echo_agent(
            instance,
            workspace,
            model=model,
            max_iterations=max_iterations,
            max_usd=max_usd,
            timeout_seconds=timeout_seconds,
            extra_args=extra_args,
        )
        patch = agent_result.get("patch", "")
        if not patch.strip():
            return InstanceResult(
                instance_id=instance.instance_id,
                error=agent_result.get("error") or "empty_patch",
                duration_seconds=time.time() - start,
                workspace=str(workspace),
                event_count=agent_result.get("event_count", 0),
            )
        return InstanceResult(
            instance_id=instance.instance_id,
            prediction=SwebenchPrediction(
                instance_id=instance.instance_id,
                model=model,
                prediction=patch,
            ),
            duration_seconds=time.time() - start,
            workspace=str(workspace),
            event_count=agent_result.get("event_count", 0),
            patch_lines=len(patch.splitlines()),
        )
    except Exception as e:
        logger.exception("[%s] Failed", instance.instance_id)
        return InstanceResult(
            instance_id=instance.instance_id,
            error=f"{type(e).__name__}: {e}",
            duration_seconds=time.time() - start,
        )


# ── Prediction file I/O ─────────────────────────────────────


def write_predictions(
    results: list[InstanceResult],
    output_path: Path,
    *,
    model_name: str,
) -> int:
    """Write predictions.jsonl from results.

    Returns the number of predictions written.
    Skips results without a prediction (errors / empty patches).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for result in results:
            if result.prediction is None:
                continue
            f.write(result.prediction.to_jsonl() + "\n")
            count += 1
    logger.info("Wrote %d predictions to %s", count, output_path)
    return count


def load_existing_predictions(path: Path) -> set[str]:
    """Load instance_ids from an existing predictions file (for resume)."""
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ids.add(entry.get("instance_id", ""))
            except json.JSONDecodeError:
                continue
    return ids


# ── Summary report ──────────────────────────────────────────


def write_summary(
    results: list[InstanceResult],
    output_path: Path,
    *,
    dataset: str,
    model: str,
) -> None:
    """Write a human-readable summary report alongside predictions."""
    total = len(results)
    succeeded = sum(1 for r in results if r.prediction is not None)
    failed = total - succeeded
    total_duration = sum(r.duration_seconds for r in results)
    total_patch_lines = sum(r.patch_lines for r in results)

    errors_by_type: dict[str, int] = {}
    for r in results:
        if r.error:
            error_type = r.error.split(":")[0].split("(")[0].strip()
            errors_by_type[error_type] = errors_by_type.get(error_type, 0) + 1

    summary = {
        "dataset": dataset,
        "model": model,
        "total_instances": total,
        "predictions_generated": succeeded,
        "failed": failed,
        "total_duration_seconds": round(total_duration, 1),
        "avg_duration_seconds": round(total_duration / max(total, 1), 1),
        "total_patch_lines": total_patch_lines,
        "avg_patch_lines": round(total_patch_lines / max(succeeded, 1), 1),
        "errors_by_type": errors_by_type,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    report_path = output_path.with_suffix(".summary.json")
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info(
        "Summary: %d/%d predictions generated (%.1f%%), %d failed, %.0fs total",
        succeeded,
        total,
        succeeded / max(total, 1) * 100,
        failed,
        total_duration,
    )


# ── Git helper ──────────────────────────────────────────────


def _run_git(
    repo_dir: Path,
    argv: list[str],
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Run a git command and return stdout/stderr/exit_code."""
    full_argv = ["git", "-C", str(repo_dir), *argv]
    try:
        completed = subprocess.run(
            full_argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"git timeout after {timeout_s}s",
            "exit_code": -1,
        }


# ── CLI entry point ─────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate SWE-bench predictions using Echo Agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:

  # Smoke test: 10 instances from SWE-bench Lite
  python -m benchmarks.swebench_adapter \\
      --dataset princeton-nlp/SWE-bench_Lite \\
      --max-samples 10 \\
      --output predictions.jsonl

  # Full run on SWE-bench Verified with 4 parallel workers
  python -m benchmarks.swebench_adapter \\
      --dataset princeton-nlp/SWE-bench_Verified \\
      --output predictions.jsonl \\
      --max-workers 4

  # Then evaluate with the official SWE-bench harness:
  python -m swebench.harness.run_evaluation \\
      --dataset_name princeton-nlp/SWE-bench_Verified \\
      --predictions_path predictions.jsonl \\
      --max_workers 16 \\
      --run_id echo_v020
""",
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help="HuggingFace dataset name (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output predictions.jsonl path",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=DEFAULT_WORKSPACE_ROOT,
        help="Root directory for isolated workspaces (default: %(default)s)",
    )
    parser.add_argument(
        "--repos-cache",
        type=Path,
        default=None,
        help="Shared git clone cache directory (default: <workspace-root>/_repo_cache)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="LLM model for the agent (default: %(default)s)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help="Max ReAct iterations per instance (default: %(default)s)",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=DEFAULT_MAX_USD,
        help="Max USD budget per instance (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Timeout per instance in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help="Process only the first N instances (for testing)",
    )
    parser.add_argument(
        "--instance-ids",
        nargs="*",
        default=None,
        help="Specific instance IDs to process (overrides --max-samples)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Number of parallel instances to process (default: %(default)s)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip instances already present in the output file",
    )
    parser.add_argument(
        "--extra-args",
        nargs="*",
        default=None,
        help="Extra arguments to pass to echo-agent code",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load instances
    instances = load_instances(
        args.dataset,
        max_samples=args.max_samples,
        instance_ids=args.instance_ids,
    )
    if not instances:
        logger.error("No instances to process")
        return 1

    # Resume: skip already-predicted instances
    existing_ids: set[str] = set()
    if args.resume:
        existing_ids = load_existing_predictions(args.output)
        if existing_ids:
            before = len(instances)
            instances = [i for i in instances if i.instance_id not in existing_ids]
            logger.info(
                "Resume: skipping %d already-predicted instances, %d remaining",
                before - len(instances),
                len(instances),
            )
    if not instances:
        logger.info("All instances already predicted. Nothing to do.")
        return 0

    # Process instances
    all_results: list[InstanceResult] = []

    # Collect existing results if resuming
    if args.resume and existing_ids and args.output.exists():
        with args.output.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    all_results.append(
                        InstanceResult(
                            instance_id=entry["instance_id"],
                            prediction=SwebenchPrediction(
                                instance_id=entry["instance_id"],
                                model=entry.get("model", args.model),
                                prediction=entry.get("prediction", ""),
                            ),
                        )
                    )
                except (json.JSONDecodeError, KeyError):
                    continue

    if args.max_workers == 1:
        # Sequential processing
        for i, instance in enumerate(instances, 1):
            logger.info("[%d/%d] Processing %s", i, len(instances), instance.instance_id)
            result = process_instance(
                instance,
                args.workspace_root,
                model=args.model,
                max_iterations=args.max_iterations,
                max_usd=args.max_usd,
                timeout_seconds=args.timeout,
                repos_cache=args.repos_cache,
                extra_args=args.extra_args,
            )
            all_results.append(result)

            # Incremental write (for crash recovery)
            if result.prediction is not None:
                _append_prediction(args.output, result.prediction)
    else:
        # Parallel processing
        with ProcessPoolExecutor(max_workers=args.max_workers) as pool:
            futures = {
                pool.submit(
                    process_instance,
                    instance,
                    args.workspace_root,
                    model=args.model,
                    max_iterations=args.max_iterations,
                    max_usd=args.max_usd,
                    timeout_seconds=args.timeout,
                    repos_cache=args.repos_cache,
                    extra_args=args.extra_args,
                ): instance
                for instance in instances
            }
            for i, future in enumerate(as_completed(futures), 1):
                instance = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = InstanceResult(
                        instance_id=instance.instance_id,
                        error=f"{type(e).__name__}: {e}",
                    )
                all_results.append(result)
                logger.info(
                    "[%d/%d] %s: %s",
                    i,
                    len(instances),
                    instance.instance_id,
                    "OK" if result.prediction else f"FAIL ({result.error})",
                )
                if result.prediction is not None:
                    _append_prediction(args.output, result.prediction)

        # Sort results by instance_id for deterministic output
        all_results.sort(key=lambda r: r.instance_id)

    # Final write (overwrite with sorted, complete predictions)
    write_predictions(all_results, args.output, model_name=args.model)
    write_summary(all_results, args.output, dataset=args.dataset, model=args.model)

    succeeded = sum(1 for r in all_results if r.prediction is not None)
    total = len(all_results)
    logger.info(
        "Done: %d/%d predictions generated (%.1f%%)",
        succeeded,
        total,
        succeeded / max(total, 1) * 100,
    )
    return 0 if succeeded > 0 else 1


def _append_prediction(path: Path, prediction: SwebenchPrediction) -> None:
    """Append a single prediction to the output file (for incremental writes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(prediction.to_jsonl() + "\n")


if __name__ == "__main__":
    sys.exit(main())


