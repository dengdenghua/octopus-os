from __future__ import annotations

import base64
import binascii
import struct
import time
import zlib
from pathlib import Path

from runtime.memory.learning.review_queue import ReviewQueue
from runtime.safety.evolution.browser_desktop_repair_recipes import (
    QUEUE_SCHEMA,
    RECIPE_SCHEMA,
    SCHEMA,
    STALE_REJECTION_SCHEMA,
    VERIFICATION_SCHEMA,
    attach_browser_desktop_repair_recipe_evidence,
    compute_browser_desktop_repair_recipe_verifications,
    compute_browser_desktop_repair_recipes,
    queue_browser_desktop_repair_recipes,
    reject_stale_browser_desktop_replay_artifacts,
    rerun_browser_desktop_repair_recipe_batch,
    rerun_browser_desktop_repair_recipe_evidence,
)


def test_browser_desktop_repair_recipes_cluster_pixel_cases(tmp_path: Path) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    for index in range(2):
        queue.upsert_item(
            source="browser_pixel_replay_gate",
            source_kind="browser_desktop_replay",
            candidate_kind="browser_pixel_replay_gate_case",
            priority="P0",
            target_bucket="browser_desktop_replay",
            title=f"Review browser pixel replay gate: shot-{index}.png",
            text="Browser pixel replay gate case failed.",
            metadata={
                "schema": "echo.browser_pixel_replay_gate_case.v1",
                "case_id": f"browser-pixel::shot-{index}.png",
                "replay": {
                    "case_id": f"browser-pixel::shot-{index}.png",
                    "fingerprint": f"fp-{index}",
                },
                "replay_gate": {
                    "passed": False,
                    "reason": "browser_pixel_evidence_failed",
                },
                "artifact": {"filename": f"shot-{index}.png", "width": 1280, "height": 720},
                "replay_gate_case": {
                    "schema": "echo.browser_pixel_replay_gate_case.v1",
                    "failures": [
                        {
                            "reason": "not enough changed pixels",
                            "thresholds": {"min_changed_ratio": 0.01},
                        },
                    ],
                },
            },
            tags=["browser", "pixel", "replay_case"],
        )

    report = compute_browser_desktop_repair_recipes(review_queue_path=queue_path)
    recipe = report["recipes"][0]

    assert report["schema"] == SCHEMA
    assert report["total_pending_cases"] == 2
    assert report["recipe_count"] == 1
    assert recipe["schema"] == RECIPE_SCHEMA
    assert recipe["priority"] == "P0"
    assert recipe["occurrences"] == 2
    assert recipe["case_ids"] == [
        "browser-pixel::shot-0.png",
        "browser-pixel::shot-1.png",
    ]
    assert recipe["evidence_summary"]["failure_reason"] == "not enough changed pixels"
    assert "fresh before/after screenshot" in " ".join(recipe["recommended_steps"])
    assert recipe["promotion_gate"]["requires_replay_rerun"] is True


def test_browser_desktop_repair_recipes_queue_dedupes(tmp_path: Path) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    queue.upsert_item(
        source="browser_session_replay",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_session_replay_case",
        priority="P1",
        target_bucket="browser_desktop_replay",
        title="Review browser replay case: workspace",
        text="Browser replay needs review.",
        metadata={
            "schema": "echo.browser_session_replay_case.v1",
            "case_id": "browser-session:workspace",
            "fingerprint": "abcdef0123456789",
            "session_id": "workspace",
            "health": {"healthy": False, "issues": ["page_crashed"]},
            "last_action": {"action": "navigate", "status": "failed"},
            "action_count": 3,
        },
        tags=["browser", "replay_case"],
    )

    result = queue_browser_desktop_repair_recipes(review_queue_path=queue_path)
    again = queue_browser_desktop_repair_recipes(review_queue_path=queue_path)

    assert result["schema"] == QUEUE_SCHEMA
    assert result["created"] == 1
    assert result["updated"] == 0
    assert again["created"] == 0
    assert again["updated"] == 1
    item = result["items"][0]
    assert item["target_bucket"] == "browser_desktop_repair_recipe"
    assert item["source"] == "browser_desktop_repair_recipe"
    assert item["metadata"]["recipe"]["candidate_kind"] == "browser_session_replay_case"
    assert "repair_recipe" in item["tags"]


def test_reject_stale_browser_desktop_replay_artifacts(tmp_path: Path) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    stale_item = queue.upsert_item(
        source="browser_pixel_replay_gate",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_pixel_replay_gate_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review stale browser pixel replay gate",
        text="Browser pixel replay gate needs review.",
        metadata={"artifact": {"local_path": str(tmp_path / "missing.png")}},
        tags=["browser", "pixel", "replay_case"],
    )["items"][0]
    live_path = tmp_path / "live.png"
    live_path.write_bytes(b"not a real png but still present")
    queue.upsert_item(
        source="browser_pixel_replay_gate",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_pixel_replay_gate_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review live browser pixel replay gate",
        text="Browser pixel replay gate needs review.",
        metadata={"artifact": {"local_path": str(live_path)}},
        tags=["browser", "pixel", "replay_case"],
    )
    stale_desktop = queue.upsert_item(
        source="computer_activity_replay",
        source_kind="browser_desktop_replay",
        candidate_kind="computer_activity_replay_case",
        priority="P1",
        target_bucket="browser_desktop_replay",
        title="Review stale computer activity replay",
        text="Computer activity replay needs review.",
        metadata={"last_activity": {"created_at": time.time() - 1000}},
        tags=["computer", "desktop", "replay_case"],
    )["items"][0]

    result = reject_stale_browser_desktop_replay_artifacts(
        review_queue_path=queue_path,
    )
    summary = queue.summary()

    assert result["schema"] == STALE_REJECTION_SCHEMA
    assert result["inspected"] == 3
    assert result["rejected_count"] == 2
    assert result["archived_recipe_count"] == 0
    assert result["rejected"][0]["id"] == stale_item["id"]
    assert result["rejected"][1]["id"] == stale_desktop["id"]
    assert summary["by_status"] == {"pending": 1, "rejected": 2}


def test_reject_stale_browser_desktop_replay_artifacts_archives_stale_recipe(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    queue.upsert_item(
        source="browser_pixel_replay_gate",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_pixel_replay_gate_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review stale browser pixel replay gate",
        text="Browser pixel replay gate needs review.",
        metadata={
            "case_id": "browser-pixel::stale.png",
            "artifact": {
                "local_path": str(tmp_path / "missing.png"),
                "width": 1280,
                "height": 720,
            },
        },
        tags=["browser", "pixel", "replay_case"],
    )
    queued = queue_browser_desktop_repair_recipes(review_queue_path=queue_path)
    recipe_item = queued["items"][0]

    result = reject_stale_browser_desktop_replay_artifacts(
        review_queue_path=queue_path,
    )
    verification = compute_browser_desktop_repair_recipe_verifications(
        review_queue_path=queue_path,
    )
    recipe_rows = ReviewQueue(queue_path).items(
        target_bucket="browser_desktop_repair_recipe",
    )["items"]

    assert result["rejected_count"] == 1
    assert result["archived_recipe_count"] == 1
    assert result["archived_recipes"][0]["id"] == recipe_item["id"]
    assert recipe_rows[0]["status"] == "archived"
    assert verification["total"] == 0
    assert verification["ready"] is True


def test_browser_desktop_repair_recipe_verifications_require_rerun_evidence(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    replay_item = queue.upsert_item(
        source="browser_session_replay",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_session_replay_case",
        priority="P1",
        target_bucket="browser_desktop_replay",
        title="Review browser replay case: workspace",
        text="Browser replay needs review.",
        metadata={
            "schema": "echo.browser_session_replay_case.v1",
            "case_id": "browser-session:workspace",
            "fingerprint": "abcdef0123456789",
            "session_id": "workspace",
            "health": {"healthy": False, "issues": ["page_crashed"]},
            "last_action": {"action": "navigate", "status": "failed"},
            "action_count": 3,
        },
        tags=["browser", "replay_case"],
    )["items"][0]
    queue_browser_desktop_repair_recipes(review_queue_path=queue_path)

    report = compute_browser_desktop_repair_recipe_verifications(
        review_queue_path=queue_path,
    )
    verification = report["verifications"][0]

    assert report["schema"] == VERIFICATION_SCHEMA
    assert report["ready"] is False
    assert report["blocked_count"] == 1
    assert verification["status"] == "needs_rerun_evidence"
    assert verification["source_status_counts"] == {"pending": 1}
    assert set(verification["blockers"]) == {
        "source_replay_cases_pending",
        "missing_verification_evidence",
        "missing_required_evidence",
    }
    assert verification["item_id"] != replay_item["id"]


def test_browser_desktop_repair_recipe_evidence_can_verify_recipe(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    replay_item = queue.upsert_item(
        source="browser_session_replay",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_session_replay_case",
        priority="P1",
        target_bucket="browser_desktop_replay",
        title="Review browser replay case: workspace",
        text="Browser replay needs review.",
        metadata={
            "schema": "echo.browser_session_replay_case.v1",
            "case_id": "browser-session:workspace",
            "fingerprint": "abcdef0123456789",
            "session_id": "workspace",
            "health": {"healthy": False, "issues": ["page_crashed"]},
            "last_action": {"action": "navigate", "status": "failed"},
            "action_count": 3,
        },
        tags=["browser", "replay_case"],
    )["items"][0]
    queued = queue_browser_desktop_repair_recipes(review_queue_path=queue_path)
    recipe_item = queued["items"][0]
    queue.decide(
        replay_item["id"],
        action="promoted",
        promoted_to="browser_desktop_replay",
    )

    attachment = attach_browser_desktop_repair_recipe_evidence(
        item_id=recipe_item["id"],
        passed=True,
        provided=["browser_session_replay_case", "session_health"],
        artifacts=[
            {
                "type": "api_check",
                "url": "/api/browser/session/health?session_id=workspace",
                "ok": True,
            }
        ],
        notes="Fresh replay and session health passed.",
        review_queue_path=queue_path,
    )
    report = compute_browser_desktop_repair_recipe_verifications(
        review_queue_path=queue_path,
    )

    assert attachment["evidence"]["schema"] == ("echo.browser_desktop_repair_recipe_evidence.v1")
    assert attachment["verification"]["status"] == "verified"
    assert report["ready"] is True
    assert report["verified_count"] == 1
    assert report["blocked_count"] == 0
    assert report["verifications"][0]["blockers"] == []


def test_browser_desktop_repair_recipe_rerun_can_verify_browser_session_recipe(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    queue.upsert_item(
        source="browser_session_replay",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_session_replay_case",
        priority="P1",
        target_bucket="browser_desktop_replay",
        title="Review browser replay case: workspace",
        text="Browser replay needs review.",
        metadata={
            "schema": "echo.browser_session_replay_case.v1",
            "case_id": "browser-session:workspace",
            "fingerprint": "abcdef0123456789",
            "session_id": "workspace",
            "health": {"healthy": False, "issues": ["page_crashed"]},
            "last_action": {"action": "navigate", "status": "failed"},
            "action_count": 3,
        },
        tags=["browser", "replay_case"],
    )
    recipe_item = queue_browser_desktop_repair_recipes(
        review_queue_path=queue_path,
    )["items"][0]

    def fake_get(path: str) -> dict:
        if "replay-case" in path:
            return {
                "schema": "echo.browser_session_replay_case.v1",
                "case_id": "browser-session:workspace",
                "replay_ready": True,
            }
        if "health" in path:
            return {"healthy": True, "replay_ready": True}
        raise AssertionError(path)

    result = rerun_browser_desktop_repair_recipe_evidence(
        item_id=recipe_item["id"],
        review_queue_path=queue_path,
        promote_source_cases=True,
        api_get=fake_get,
        api_request=lambda _method, _path, _body: {"ok": True},
    )

    assert result["passed"] is True
    assert result["provided"] == ["browser_session_replay_case", "session_health"]
    assert result["promoted_source_count"] == 1
    assert result["attachment"]["verification"]["status"] == "verified"


def test_browser_desktop_repair_recipe_rerun_does_not_fake_pixel_evidence(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    queue.upsert_item(
        source="browser_pixel_replay_gate",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_pixel_replay_gate_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review browser pixel replay gate",
        text="Browser pixel replay gate needs review.",
        metadata={
            "schema": "echo.browser_pixel_replay_gate_case.v1",
            "case_id": "browser-pixel::shot.png",
            "artifact": {"filename": "shot.png", "width": 1280, "height": 720},
            "replay_gate_case": {
                "failures": [{"reason": "not enough changed pixels"}],
            },
        },
        tags=["browser", "pixel", "replay_case"],
    )
    recipe_item = queue_browser_desktop_repair_recipes(
        review_queue_path=queue_path,
    )["items"][0]

    result = rerun_browser_desktop_repair_recipe_evidence(
        item_id=recipe_item["id"],
        review_queue_path=queue_path,
        api_get=lambda _path: {"ok": True, "ready": True},
        api_request=lambda _method, _path, _body: {"ok": True},
    )

    assert result["passed"] is False
    assert result["provided"] == []
    assert result["missing"] == ["fresh_screenshot", "pixel_comparison"]
    assert result["attachment"]["verification"]["status"] == "needs_rerun_evidence"


def test_browser_desktop_repair_recipe_rerun_can_verify_pixel_recipe(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    queue.upsert_item(
        source="browser_pixel_replay_gate",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_pixel_replay_gate_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review browser pixel replay gate",
        text="Browser pixel replay gate needs review.",
        metadata={
            "schema": "echo.browser_pixel_replay_gate_case.v1",
            "case_id": "browser-pixel::shot.png",
            "artifact": {"filename": "shot.png", "width": 2, "height": 2},
            "replay_gate_case": {
                "failures": [
                    {
                        "reason": "not enough changed pixels",
                        "thresholds": {"min_changed_ratio": 0.01},
                    },
                ],
            },
        },
        tags=["browser", "pixel", "replay_case"],
    )
    recipe_item = queue_browser_desktop_repair_recipes(
        review_queue_path=queue_path,
    )["items"][0]
    screenshots = [
        _png(
            2,
            2,
            [
                (0, 0, 0, 255),
                (255, 255, 255, 255),
                (255, 255, 255, 255),
                (255, 255, 255, 255),
            ],
        ),
        _png(
            2,
            2,
            [
                (255, 0, 0, 255),
                (255, 255, 255, 255),
                (255, 255, 255, 255),
                (255, 255, 255, 255),
            ],
        ),
    ]

    def fake_get(path: str) -> dict:
        if "/api/browser/screenshot/base64" in path:
            screenshot = screenshots.pop(0)
            return {"base64": base64.b64encode(screenshot).decode("ascii")}
        return {"ok": True, "ready": True}

    result = rerun_browser_desktop_repair_recipe_evidence(
        item_id=recipe_item["id"],
        review_queue_path=queue_path,
        promote_source_cases=True,
        api_get=fake_get,
        api_request=lambda _method, _path, _body: {"ok": True},
    )

    assert result["passed"] is True
    assert result["provided"] == ["fresh_screenshot", "pixel_comparison"]
    assert result["promoted_source_count"] == 1
    assert result["attachment"]["verification"]["status"] == "verified"


def test_browser_desktop_repair_recipe_rerun_batch_reports_pass_and_fail(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    queue.upsert_item(
        source="browser_session_replay",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_session_replay_case",
        priority="P1",
        target_bucket="browser_desktop_replay",
        title="Review browser replay case: workspace",
        text="Browser replay needs review.",
        metadata={
            "schema": "echo.browser_session_replay_case.v1",
            "case_id": "browser-session:workspace",
            "fingerprint": "abcdef0123456789",
            "session_id": "workspace",
            "health": {"healthy": False, "issues": ["page_crashed"]},
            "last_action": {"action": "navigate", "status": "failed"},
            "action_count": 3,
        },
        tags=["browser", "replay_case"],
    )
    queue.upsert_item(
        source="browser_pixel_replay_gate",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_pixel_replay_gate_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review browser pixel replay gate",
        text="Browser pixel replay gate needs review.",
        metadata={
            "schema": "echo.browser_pixel_replay_gate_case.v1",
            "case_id": "browser-pixel::shot.png",
            "artifact": {"filename": "shot.png", "width": 1280, "height": 720},
            "replay_gate_case": {
                "failures": [{"reason": "not enough changed pixels"}],
            },
        },
        tags=["browser", "pixel", "replay_case"],
    )
    queue_browser_desktop_repair_recipes(review_queue_path=queue_path)

    def fake_get(path: str) -> dict:
        if "replay-case" in path:
            return {"replay_ready": True}
        if "health" in path:
            return {"healthy": True}
        return {"ok": True}

    result = rerun_browser_desktop_repair_recipe_batch(
        review_queue_path=queue_path,
        promote_source_cases=True,
        api_get=fake_get,
        api_request=lambda _method, _path, _body: {"ok": True},
    )

    assert result["schema"] == "echo.browser_desktop_repair_recipe_rerun_batch.v1"
    assert result["attempted"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1


def test_screenshot_path_failure_recipe_reruns_production_contract_and_promotes(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "review_queue.json"
    queue = ReviewQueue(queue_path)
    source = queue.upsert_item(
        source="computer_use_loop_failure",
        source_kind="browser_desktop_replay",
        candidate_kind="computer_activity_replay_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review computer-use loop failure",
        text="Planner could not read the first captured screenshot.",
        metadata={
            "case_id": "culoop:planner_gave_up:fail:path-contract",
            "fingerprint": "culoop:planner_gave_up:fail:path-contract",
            "last_activity": {
                "event": "planner_gave_up",
                "action": {"action": "fail"},
            },
            "goal": "inspect a sandboxed desktop screenshot",
            "reason": (
                "screenshot read failed: [Errno 2] No such file or directory: 'iter_000.png'"
            ),
            "iterations": 1,
        },
        tags=["computer", "desktop", "failure"],
    )["items"][0]

    queued = queue_browser_desktop_repair_recipes(review_queue_path=queue_path)
    recipe = queued["recipes"][0]
    recipe_item = queued["items"][0]

    assert recipe["verification_plan"]["api_checks"] == []
    assert recipe["verification_plan"]["evidence_required"] == ["computer_screenshot_path_contract"]

    result = rerun_browser_desktop_repair_recipe_evidence(
        item_id=recipe_item["id"],
        review_queue_path=queue_path,
        promote_source_cases=True,
        actor="regression_test",
    )

    assert result["passed"] is True
    assert result["provided"] == ["computer_screenshot_path_contract"]
    assert result["promoted_source_count"] == 1
    artifact = result["artifacts"][0]
    assert artifact["schema"] == "echo.computer_screenshot_path_contract.v1"
    assert artifact["captured_path_is_authoritative"] is True
    assert artifact["screenshot_bytes"] > 8
    assert len(artifact["screenshot_sha256"]) == 64
    source_after = next(
        item
        for item in queue.items(target_bucket="browser_desktop_replay", limit=10)["items"]
        if item["id"] == source["id"]
    )
    assert source_after["status"] == "promoted"
    verification = result["attachment"]["verification"]
    assert verification["status"] == "verified"


def _png(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for pixel in pixels[y * width : (y + 1) * width]:
            rows.extend(pixel)
    raw = zlib.compress(bytes(rows))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", raw)
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


