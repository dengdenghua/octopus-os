"""
Integration tests for ``PromptRegistry`` and ``prompts_router``.

Coverage
--------
1. Registry round-trip · ``set()`` then ``get()`` returns the body
2. Variant fallback · missing variant falls through to base
3. Hot reload · external edit visible only when the flag is ON
4. Atomic write · ``PUT`` overwrite produces ``.bak`` sibling
5. Flag-off 403 · ``PUT`` and ``POST /reload`` are gated
6. Flag-on 200 · same endpoints succeed once the flag flips
7. ``GET /api/prompts`` returns all registered names
8. mtime cache stickiness with flag OFF
9. Concurrent reads don't crash

Design notes
------------
* Each test gets a fresh ``tmp_path``-rooted ``PromptRegistry`` and a
  matching ``FastAPI`` app wired with the router.  No CWD redirection
  needed — the registry is path-injected.
* The ``ui.prompts_hot_reload`` flag is process-global, so we restore
  it via ``monkeypatch.setenv`` in tests that flip it.  The autouse
  ``_reset_flags`` fixture forces a re-resolve before *and* after each
  test so cross-test bleed is impossible.
* mtime tests force a tiny ``time.sleep`` after edits.  Some
  filesystems (notably FAT, but also tmpfs in containers) only have
  second-resolution mtime — sleeping past the next tick is the
  cheapest robust way.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.platform.prompts.registry import PromptRegistry
from runtime.sensing.gateway.prompts_router import create_prompts_router

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_flags() -> Iterator[None]:
    """The feature-flag registry caches a process-wide snapshot.  Force
    a reload before and after each test so OS environ changes from one
    test never leak into another.
    """
    from runtime.platform import feature_flags as _ff

    # Drop any env override the prior test might have set
    os.environ.pop("ECHO_FF_UI_PROMPTS_HOT_RELOAD", None)
    _ff.reload()
    yield
    os.environ.pop("ECHO_FF_UI_PROMPTS_HOT_RELOAD", None)
    _ff.reload()


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    return d


@pytest.fixture
def registry(prompts_dir: Path) -> PromptRegistry:
    return PromptRegistry(prompts_dir)


@pytest.fixture
def client(registry: PromptRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(create_prompts_router(registry))
    return TestClient(app)


def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch ``ui.prompts_hot_reload`` ON and reload the snapshot."""
    from runtime.platform import feature_flags as _ff

    monkeypatch.setenv("ECHO_FF_UI_PROMPTS_HOT_RELOAD", "1")
    _ff.reload()


def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch ``ui.prompts_hot_reload`` OFF and reload the snapshot."""
    from runtime.platform import feature_flags as _ff

    monkeypatch.setenv("ECHO_FF_UI_PROMPTS_HOT_RELOAD", "0")
    _ff.reload()


# ═══════════════════════════════════════════════════════════
# 1. Registry round-trip
# ═══════════════════════════════════════════════════════════


class TestRoundTrip:
    def test_set_then_get_returns_body(
        self,
        registry: PromptRegistry,
    ) -> None:
        # ``atomic_write_text`` appends a trailing newline by design
        # (POSIX-friendly).  We assert by ``startswith`` so the test
        # locks the *content* contract without baking in the newline
        # quirk.
        registry.set("system_prompt", "You are Echo.")
        body = registry.get("system_prompt")
        assert body.startswith("You are Echo.")

    def test_set_writes_md_file_to_disk(
        self,
        registry: PromptRegistry,
        prompts_dir: Path,
    ) -> None:
        registry.set("hello", "hi there")
        assert (prompts_dir / "hello.md").exists()
        # Content is on disk verbatim (atomic_write_text appends a
        # trailing newline by default — that's fine for Markdown).
        text = (prompts_dir / "hello.md").read_text(encoding="utf-8")
        assert "hi there" in text

    def test_variant_set_lands_in_subdir(
        self,
        registry: PromptRegistry,
        prompts_dir: Path,
    ) -> None:
        registry.set("sys", "base body")
        registry.set("sys", "friendly body", variant="friendly")
        assert (prompts_dir / "variants" / "sys" / "friendly.md").exists()
        assert registry.get("sys", variant="friendly").startswith(
            "friendly body",
        )


# ═══════════════════════════════════════════════════════════
# 2. Variant fallback
# ═══════════════════════════════════════════════════════════


class TestVariantFallback:
    def test_missing_variant_falls_back_to_base(
        self,
        registry: PromptRegistry,
    ) -> None:
        registry.set("greeting", "hello base")
        # No variant file exists
        assert registry.get(
            "greeting",
            variant="cheerful",
        ).startswith("hello base")

    def test_missing_everything_raises(
        self,
        registry: PromptRegistry,
    ) -> None:
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_variant_takes_precedence_over_base(
        self,
        registry: PromptRegistry,
    ) -> None:
        registry.set("greeting", "base")
        registry.set("greeting", "variant!", variant="alt")
        assert registry.get("greeting", variant="alt").startswith(
            "variant!",
        )
        # Base still reachable
        assert registry.get("greeting").startswith("base")


# ═══════════════════════════════════════════════════════════
# 3. Hot reload behavior (flag toggles)
# ═══════════════════════════════════════════════════════════


class TestHotReload:
    def test_flag_on_picks_up_disk_edit(
        self,
        registry: PromptRegistry,
        prompts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry.set("p", "original")
        assert registry.get("p").startswith("original")

        # Flip flag ON, then bash the file directly (simulating an
        # editor save outside the registry).
        _flag_on(monkeypatch)
        time.sleep(1.1)  # ensure mtime advances on coarse-grained FS
        (prompts_dir / "p.md").write_text("edited", encoding="utf-8")

        assert registry.get("p") == "edited"

    def test_flag_off_does_not_pick_up_disk_edit(
        self,
        registry: PromptRegistry,
        prompts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry.set("p", "original")
        _flag_off(monkeypatch)

        time.sleep(1.1)
        (prompts_dir / "p.md").write_text("edited", encoding="utf-8")

        # Sticky cache wins
        assert registry.get("p").startswith("original")
        assert "edited" not in registry.get("p")

    def test_explicit_reload_picks_up_changes_with_flag_off(
        self,
        registry: PromptRegistry,
        prompts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``reload()`` is unconditional — it bypasses the flag check.
        That's what makes ``POST /api/prompts/reload`` useful."""
        registry.set("p", "v1")
        _flag_off(monkeypatch)
        (prompts_dir / "p.md").write_text("v2", encoding="utf-8")
        registry.reload()
        assert registry.get("p") == "v2"


# ═══════════════════════════════════════════════════════════
# 4. Atomic write through PUT (.bak sibling appears)
# ═══════════════════════════════════════════════════════════


class TestAtomicPut:
    def test_overwrite_creates_bak(
        self,
        client: TestClient,
        prompts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _flag_on(monkeypatch)

        # First write — no .bak yet
        r = client.put(
            "/api/prompts/sys",
            json={"content": "first"},
        )
        assert r.status_code == 200
        assert (prompts_dir / "sys.md").exists()
        assert not (prompts_dir / "sys.md.bak").exists()

        # Second write — previous lands in .bak
        r2 = client.put(
            "/api/prompts/sys",
            json={"content": "second"},
        )
        assert r2.status_code == 200
        assert (prompts_dir / "sys.md.bak").exists()
        assert (
            (prompts_dir / "sys.md")
            .read_text(
                encoding="utf-8",
            )
            .startswith("second")
        )
        # The .bak holds the prior version (atomic_write_text appends
        # a trailing newline; assert by prefix to remain robust).
        assert (
            (prompts_dir / "sys.md.bak")
            .read_text(
                encoding="utf-8",
            )
            .startswith("first")
        )


# ═══════════════════════════════════════════════════════════
# 5. Flag OFF → 403 on PUT and reload
# ═══════════════════════════════════════════════════════════


class TestFlagGating:
    def test_put_403_when_flag_off(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _flag_off(monkeypatch)
        r = client.put(
            "/api/prompts/sys",
            json={"content": "x"},
        )
        assert r.status_code == 403
        assert "prompts_hot_reload_disabled" in r.text

    def test_reload_403_when_flag_off(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _flag_off(monkeypatch)
        r = client.post("/api/prompts/reload")
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════
# 6. Flag ON → 200
# ═══════════════════════════════════════════════════════════


class TestFlagEnabled:
    def test_put_200_when_flag_on(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _flag_on(monkeypatch)
        r = client.put(
            "/api/prompts/foo",
            json={"content": "bar"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_reload_200_when_flag_on(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _flag_on(monkeypatch)
        r = client.post("/api/prompts/reload")
        assert r.status_code == 200
        assert "prompts" in r.json()

    def test_get_works_regardless_of_flag(
        self,
        client: TestClient,
        registry: PromptRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry.set("seeded", "ok")
        # Flag OFF — read still works
        _flag_off(monkeypatch)
        r = client.get("/api/prompts/seeded")
        assert r.status_code == 200
        assert r.json()["content"].startswith("ok")


# ═══════════════════════════════════════════════════════════
# 7. List endpoint
# ═══════════════════════════════════════════════════════════


class TestListEndpoint:
    def test_returns_all_registered_names(
        self,
        client: TestClient,
        registry: PromptRegistry,
    ) -> None:
        registry.set("alpha", "a")
        registry.set("bravo", "b")
        registry.set("alpha", "a-friendly", variant="friendly")
        registry.set("alpha", "a-pragmatic", variant="pragmatic")

        r = client.get("/api/prompts")
        assert r.status_code == 200
        data = r.json()
        assert "prompts" in data

        names = {p["name"] for p in data["prompts"]}
        assert names == {"alpha", "bravo"}

        alpha = next(p for p in data["prompts"] if p["name"] == "alpha")
        assert alpha["variants"] == ["friendly", "pragmatic"]
        bravo = next(p for p in data["prompts"] if p["name"] == "bravo")
        assert bravo["variants"] == []
        # ``modified_at`` is a float (epoch seconds)
        assert isinstance(bravo["modified_at"], float)


# ═══════════════════════════════════════════════════════════
# 8. mtime caching when flag OFF
# ═══════════════════════════════════════════════════════════


class TestMtimeCaching:
    def test_external_edit_invisible_with_flag_off(
        self,
        registry: PromptRegistry,
        prompts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed via the registry so it's cached
        registry.set("ghost", "v1")
        # Read once with the flag OFF (snapshots the cache)
        _flag_off(monkeypatch)
        assert registry.get("ghost").startswith("v1")

        # External edit
        time.sleep(1.1)
        (prompts_dir / "ghost.md").write_text(
            "v2-from-disk",
            encoding="utf-8",
        )

        # Still the cached version because no re-stat occurs
        body = registry.get("ghost")
        assert body.startswith("v1")
        assert "v2-from-disk" not in body


# ═══════════════════════════════════════════════════════════
# 9. Concurrent reads
# ═══════════════════════════════════════════════════════════


class TestConcurrentReads:
    def test_many_threads_dont_crash(
        self,
        registry: PromptRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed a handful of prompts
        for i in range(5):
            registry.set(f"p{i}", f"body-{i}")
            registry.set(f"p{i}", f"v-{i}", variant="alt")
        _flag_on(monkeypatch)  # exercise the re-stat codepath

        errors: list[BaseException] = []
        stop = threading.Event()

        def worker() -> None:
            try:
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline and not stop.is_set():
                    for i in range(5):
                        assert registry.get(f"p{i}").startswith(
                            f"body-{i}",
                        )
                        assert registry.get(
                            f"p{i}",
                            variant="alt",
                        ).startswith(f"v-{i}")
                    registry.list()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        stop.set()

        assert errors == []
