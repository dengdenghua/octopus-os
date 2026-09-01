from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json

_LOG = logging.getLogger("echo.camouflage.scheduler")


@dataclass
class CamouflageConfig:
    enabled: bool = False
    interval_sec: int = 600
    initial_delay_sec: int = 60
    output_dir: str = "data"
    variants_path: str = "data/camouflage_variants.yaml"
    state_path: str = "data/camouflage_state.json"
    min_interval_seconds: float = 600.0
    min_assignments_since_last: int = 5


class CamouflageScheduler:
    _instance: CamouflageScheduler | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> CamouflageScheduler:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Stop the worker thread (if running) and drop the singleton.

        For test isolation only — production keeps a single
        CamouflageScheduler alive for the process lifetime.
        """
        with cls._lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            try:  # noqa: SIM105
                inst.stop(timeout=2.0)
            except Exception:  # noqa: BLE001 — reset must never raise
                pass

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stack: Any = None
        self._config = CamouflageConfig()
        self._lock = threading.RLock()
        self._optimizer: Any = None
        self._mutator: Any = None
        self._evolver: Any = None
        self._auto_retire: Any = None
        self._tick_count = 0
        self._last_step_summary: str = ""
        self._last_error: str = ""

    def start(
        self,
        stack: Any,
        config: CamouflageConfig | None = None,
    ) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                _LOG.info("camouflage scheduler already running · skip start")
                return
            if config is not None:
                self._config = config
            if not self._config.enabled:
                self._last_error = "disabled (ECHO_CAMOUFLAGE_ENABLED!=1)"
                _LOG.info("camouflage scheduler disabled · skip")
                return
            if stack is None or getattr(stack, "journal", None) is None:
                self._last_error = "stack/journal missing"
                _LOG.warning(
                    "camouflage scheduler: stack/journal missing · skipping",
                )
                return
            if not getattr(stack, "is_llm_planner", False):
                self._last_error = "stack.planner is not LLMPlanner"
                _LOG.warning(
                    "camouflage scheduler: requires LLMPlanner · got static · skipping",
                )
                return
            router = getattr(stack.planner, "router", None)
            if router is None:
                self._last_error = "stack.planner has no router"
                _LOG.warning(
                    "camouflage scheduler: planner.router missing · skipping",
                )
                return

            self._stack = stack
            try:
                self._build_components(router)
            except (ImportError, TypeError, AttributeError) as exc:
                self._last_error = f"build_failed: {type(exc).__name__}: {exc}"
                _LOG.warning(
                    "camouflage scheduler: component build failed · %s",
                    self._last_error,
                )
                return

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="camouflage-scheduler",
                daemon=True,
            )
            self._thread.start()
            self._last_error = ""
            _LOG.info(
                "🎭 camouflage scheduler started · interval=%ds initial_delay=%ds variants=%d",
                self._config.interval_sec,
                self._config.initial_delay_sec,
                len(self._optimizer.variant_names) if self._optimizer else 0,
            )

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stop_event.set()
            t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            out: dict[str, Any] = {
                "enabled": bool(self._config.enabled),
                "running": running,
                "tick_count": self._tick_count,
                "interval_sec": self._config.interval_sec,
                "last_error": self._last_error,
                "last_step_summary": self._last_step_summary,
            }
            if self._optimizer is not None:
                try:
                    out["variants"] = [
                        {
                            "name": v.name,
                            "weight": v.weight,
                            "origin": v.origin,
                            "generation": v.generation,
                            "parents": list(v.parents),
                            "suffix_chars": len(v.system_prompt_suffix or ""),
                        }
                        for v in self._optimizer._variants.values()
                    ]
                except (TypeError, AttributeError, ImportError, OSError):  # noqa: BLE001
                    out["variants"] = []
            else:
                out["variants"] = []
            if self._auto_retire is not None:
                stats = self._auto_retire.stats
                out["auto_retire"] = {
                    "total_ticks": stats.total_ticks,
                    "total_steps": stats.total_steps,
                    "total_retired": stats.total_retired,
                    "total_boosted": stats.total_boosted,
                    "last_step_at": stats.last_step_at,
                    "last_step_summary": stats.last_step_summary,
                }
            return out

    def assign_variant_suffix(self, task_id: str) -> tuple[str, str]:
        with self._lock:
            opt = self._optimizer
        if opt is None:
            return ("baseline", "")
        try:
            v = opt._splitter.assign_for(str(task_id))
            variant_obj, _planner = v.payload
            return (v.name, variant_obj.system_prompt_suffix or "")
        except (TypeError, AttributeError, ImportError, OSError) as exc:  # noqa: BLE001
            _LOG.debug("camouflage assign_variant_suffix failed: %s", exc)
            return ("baseline", "")

    def record_outcome(self, task_id: str, *, success: bool) -> None:
        with self._lock:
            opt = self._optimizer
        if opt is None:
            return
        try:
            opt.record_outcome(str(task_id), success=success)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("camouflage record_outcome failed: %s", exc)

    def _build_components(self, router: Any) -> None:
        from runtime.safety.experiments import (
            AutoRetireConfig,
            AutoRetireScheduler,
            EvolutionPolicy,
            PromptEvolver,
            PromptMutator,
            PromptOptimizer,
            PromptVariant,
            load_variants_from_yaml,
        )

        variants_path = Path(self._config.variants_path)
        variants: list[PromptVariant] = []
        if variants_path.is_file():
            try:
                variants = load_variants_from_yaml(variants_path)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "load_variants_from_yaml failed (%s) · falling back to seed",
                    exc,
                )
                variants = []
        if not variants:
            variants = [
                PromptVariant(
                    name="baseline",
                    system_prompt_suffix="",
                    weight=1.0,
                    description="Default · empty suffix",
                    origin="seed",
                ),
            ]

        self._optimizer = PromptOptimizer(
            self._stack,
            variants,
            sticky=True,
            auto_persist_path=str(variants_path),
        )

        self._mutator = PromptMutator(
            router=router,
            model=getattr(self._stack.planner, "planner_model", None)
            or "claude-haiku-4-5-20251001",
            max_suffix_chars=400,
            temperature=0.7,
        )

        self._evolver = PromptEvolver(
            self._optimizer,
            self._mutator,
            EvolutionPolicy(),
        )

        retire_cfg = AutoRetireConfig(
            min_interval_seconds=self._config.min_interval_seconds,
            min_assignments_since_last=self._config.min_assignments_since_last,
            min_variants_to_act=2,
        )
        self._auto_retire = AutoRetireScheduler(
            self._evolver,
            config=retire_cfg,
            bus=None,
        )

    def _run_loop(self) -> None:
        if self._stop_event.wait(timeout=self._config.initial_delay_sec):
            return
        while not self._stop_event.is_set():
            try:
                self._tick_once()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("camouflage tick failed: %s", exc)
                self._last_error = f"tick_failed: {type(exc).__name__}: {exc}"
            if self._stop_event.wait(timeout=self._config.interval_sec):
                return

    def _tick_once(self) -> None:
        with self._lock:
            self._tick_count += 1
            n = self._tick_count
        step = self._auto_retire.tick() if self._auto_retire else None
        with self._lock:
            self._last_step_summary = step.summary if step else "no-op"
        self._dump_state(n, step)
        _LOG.info(
            "🎭 camouflage tick #%d · %s",
            n,
            self._last_step_summary,
        )

    def _dump_state(self, tick: int, step: Any) -> None:
        path = Path(self._config.state_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "tick": tick,
                "ts": time.time(),
                "status": self.status(),
            }
            if step is not None:
                payload["last_step"] = {
                    "retired": list(step.retired),
                    "boosted": [
                        {"name": n, "old_weight": ow, "new_weight": nw}
                        for (n, ow, nw) in step.boosted
                    ],
                    "mutated": (step.mutation.variant.name if step.mutation else None),
                    "crossover": (step.crossover.variant.name if step.crossover else None),
                    "summary": step.summary,
                }
            try:
                atomic_write_json(path, payload, default=str)
            except (OSError, TypeError, ValueError):
                raise
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("camouflage state dump failed: %s", exc)


def get_camouflage_scheduler() -> CamouflageScheduler:
    return CamouflageScheduler.get()
