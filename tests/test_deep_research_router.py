from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.execution.parallel_agents import ParallelAgentOrchestrator
from runtime.research.deep_research import (
    DeepResearchPlanner,
    DeepResearchRequest,
    ResearchEvidence,
    ResearchRole,
)
from runtime.research.prefetch import ResearchPrefetcher
from runtime.sensing.gateway.deep_research_router import (
    _default_job_store_path,
    create_deep_research_router,
)


def test_deep_research_planner_builds_roles_sources_and_steps():
    planner = DeepResearchPlanner()
    job = planner.build_plan(
        DeepResearchRequest(
            topic="NAS市场调研",
            urls=["https://www.synology.com/"],
            max_searches=274,
            max_subagents=5,
        )
    )

    assert job.topic == "NAS市场调研"
    assert job.max_searches == 274
    assert len(job.roles) == 5
    assert len(job.steps) == 6  # 5 role steps + synthesis
    assert any(source.kind == "provided_url" for source in job.sources)
    assert any(source.kind == "academic" for source in job.sources)
    assert any(source.kind == "social" for source in job.sources)
    assert any(source.provider == "fetch_url" for source in job.sources)
    assert any(source.provider == "web_search" for source in job.sources)
    assert "web_search" in job.steps[0].prompt
    assert "queries:" in job.steps[0].prompt
    assert any(mat.url == "https://www.synology.com/" for mat in job.materials)
    assert job.steps[-1].role_id == "synthesis"
    assert any(ev.url == "https://www.synology.com/" for ev in job.evidence)


def test_default_deep_research_store_prefers_echo_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert (
        _default_job_store_path() == tmp_path / ".echo" / "research" / "deep-research-jobs.jsonl"
    )

    legacy = tmp_path / ".echo-research" / "deep-research-jobs.jsonl"
    legacy.parent.mkdir()
    legacy.write_text("", encoding="utf-8")

    assert _default_job_store_path() == legacy


def test_deep_research_dispatch_uses_virtual_ephemeral_roles():
    planner = DeepResearchPlanner()
    job = planner.build_plan(
        DeepResearchRequest(
            topic="NAS market research",
            max_subagents=5,
        )
    )

    tasks = planner.dispatch_tasks(job)
    names = {task["subagent_name"] for task in tasks}

    assert names
    assert all(name.startswith("virtual-research-") for name in names)
    assert names.isdisjoint({"researcher", "analyst", "reviewer", "general"})


def test_custom_deep_research_roles_are_virtualized():
    planner = DeepResearchPlanner()
    job = planner.build_plan(
        DeepResearchRequest(
            topic="NAS market research",
            max_subagents=2,
            roles=[
                ResearchRole(
                    id="competitor analyst",
                    name="Competitor Analyst",
                    subagent_name="researcher",
                    focus="Competitor positioning",
                    deliverable="Competitor findings",
                    search_angles=["vendor comparison"],
                ),
            ],
        )
    )

    assert len(job.roles) == 1
    assert job.roles[0].id == "competitor-analyst"
    assert job.roles[0].subagent_name == "virtual-research-competitor-analyst"
    tasks = planner.dispatch_tasks(job)
    assert tasks[0]["subagent_name"] == "virtual-research-competitor-analyst"


def test_research_prefetcher_records_search_logs():
    def search_handler(**kwargs):
        return {
            "backend": "test-search",
            "results": [
                {
                    "title": "NAS report",
                    "url": "https://example.com/nas",
                    "snippet": "market summary",
                }
            ],
        }

    planner = DeepResearchPlanner()
    job = planner.build_plan(
        DeepResearchRequest(
            topic="NAS market research",
            source_kinds=["web"],
            max_subagents=1,
        )
    )
    result = ResearchPrefetcher(
        search_handler=search_handler,
        max_queries=1,
    ).prefetch(job)

    assert len(result.evidence) == 1
    assert result.logs[0].action == "search"
    assert result.logs[0].status == "completed"
    assert result.logs[0].result_count == 1
    assert result.logs[0].evidence_count == 1


def test_plan_endpoint_includes_thread_uploads(tmp_path):
    upload_root = tmp_path / "uploads"
    thread_dir = upload_root / "t1"
    thread_dir.mkdir(parents=True)
    (thread_dir / "brief.md").write_text("market notes", encoding="utf-8")

    app = FastAPI()
    app.include_router(create_deep_research_router(upload_root=upload_root))
    client = TestClient(app)

    response = client.post(
        "/api/research/deep/plan",
        json={
            "topic": "NAS市场调研",
            "thread_id": "t1",
            "source_kinds": ["web", "uploaded_file"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert any(mat["title"] == "brief.md" for mat in data["materials"])
    assert any(src["kind"] == "uploaded_file" for src in data["sources"])


def test_plan_endpoint_merges_explicit_materials_thread_uploads_and_dedupes_urls(tmp_path):
    upload_root = tmp_path / "uploads"
    thread_dir = upload_root / "t1"
    thread_dir.mkdir(parents=True)
    (thread_dir / "brief.md").write_text("market notes", encoding="utf-8")

    app = FastAPI()
    app.include_router(create_deep_research_router(upload_root=upload_root))
    client = TestClient(app)

    response = client.post(
        "/api/research/deep/plan",
        json={
            "topic": "NAS market research",
            "thread_id": "t1",
            "materials": [
                {
                    "kind": "url",
                    "title": "Synology",
                    "url": "https://www.synology.com/",
                    "notes": "official site",
                },
                {
                    "kind": "text",
                    "title": "Internal notes",
                    "text": "Users care about backup and media streaming.",
                },
            ],
            "urls": ["https://www.synology.com/"],
            "source_kinds": ["web", "uploaded_file", "provided_url"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert any(mat["title"] == "brief.md" for mat in data["materials"])
    assert any(
        mat["kind"] == "text" and mat["title"] == "Internal notes" for mat in data["materials"]
    )
    synology_materials = [
        mat for mat in data["materials"] if mat.get("url") == "https://www.synology.com/"
    ]
    assert len(synology_materials) == 1
    assert any(ev["url"] == "https://www.synology.com/" for ev in data["evidence"])


def test_research_jobs_persist_across_router_instances(tmp_path):
    store_path = tmp_path / "research-jobs.jsonl"

    app1 = FastAPI()
    app1.include_router(create_deep_research_router(job_store_path=store_path))
    client1 = TestClient(app1)

    response = client1.post(
        "/api/research/deep/plan",
        json={
            "topic": "NAS market research",
            "lead_agent_name": "lead",
            "urls": ["https://www.synology.com/"],
        },
    )
    assert response.status_code == 200
    job = response.json()
    assert store_path.exists()

    app2 = FastAPI()
    app2.include_router(create_deep_research_router(job_store_path=store_path))
    client2 = TestClient(app2)

    restored = client2.get(f"/api/research/deep/jobs/{job['job_id']}")
    assert restored.status_code == 200
    restored_job = restored.json()
    assert restored_job["job_id"] == job["job_id"]
    assert restored_job["topic"] == "NAS market research"
    assert restored_job["lead_agent_name"] == "lead"
    assert any(mat["url"] == "https://www.synology.com/" for mat in restored_job["materials"])
    assert any(ev["url"] == "https://www.synology.com/" for ev in restored_job["evidence"])


def test_completed_research_extracts_structured_evidence(tmp_path):
    def runner(description, *, subagent_name, context=None, cancel_event=None):
        return (
            "Finding: Synology is a relevant NAS vendor.\n"
            'EVIDENCE {"title":"Synology official","url":"https://www.synology.com/",'
            '"source_kind":"company_site","claim":"Synology is a NAS vendor",'
            '"stance":"support","confidence":0.9,"quote_or_summary":"Official product site"}'
        )

    orchestrator = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
    try:
        app = FastAPI()
        app.include_router(
            create_deep_research_router(
                orchestrator=orchestrator,
                agents_root=tmp_path / "agents",
                job_store_path=tmp_path / "jobs.jsonl",
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/research/deep/start",
            json={
                "topic": "NAS market research",
                "lead_agent_name": "lead",
                "max_subagents": 1,
            },
        )
        assert response.status_code == 200
        job = response.json()
        for _ in range(100):
            batch = orchestrator.get_batch(job["dispatch_batch_id"])
            assert batch is not None
            if batch.status == "completed":
                break
            time.sleep(0.02)

        refreshed = client.get(f"/api/research/deep/jobs/{job['job_id']}")
        assert refreshed.status_code == 200
        refreshed_job = refreshed.json()
        assert any(ev["claim"] == "Synology is a NAS vendor" for ev in refreshed_job["evidence"])
        assert "Evidence Table" in refreshed_job["final_report"]
        assert "https://www.synology.com/" in refreshed_job["final_report"]
        assert "## 执行摘要" in refreshed_job["final_report"]
        assert "## 调研范围与方法" in refreshed_job["final_report"]
        assert "## 结论与建议" in refreshed_job["final_report"]
    finally:
        orchestrator.shutdown(wait=False)


def test_start_endpoint_dispatches_parallel_research_tasks(tmp_path):
    seen_contexts: list[dict] = []

    def runner(description, *, subagent_name, context=None, cancel_event=None):
        seen_contexts.append(context or {})
        return f"{subagent_name}: {description[:20]}"

    orchestrator = ParallelAgentOrchestrator(max_concurrency=4, task_runner=runner)
    try:
        app = FastAPI()
        app.include_router(
            create_deep_research_router(
                orchestrator=orchestrator,
                agents_root=tmp_path / "agents",
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/research/deep/start",
            json={
                "topic": "NAS市场调研",
                "lead_agent_name": "general",
                "max_subagents": 3,
                "max_searches": 30,
            },
        )

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "running"
        assert job["dispatch_batch_id"]
        assert job["lead_agent_name"] == "general"

        for _ in range(100):
            batch = orchestrator.get_batch(job["dispatch_batch_id"])
            assert batch is not None
            if batch.status == "completed":
                break
            time.sleep(0.02)

        batch = orchestrator.get_batch(job["dispatch_batch_id"])
        assert batch is not None
        assert batch.completed_tasks == 3
        assert seen_contexts
        assert all(ctx.get("lead_agent_name") == "general" for ctx in seen_contexts)
        assert all(ctx.get("research_ephemeral_workers") is True for ctx in seen_contexts)
        assert all(ctx.get("research_sources") for ctx in seen_contexts)
        assert all(
            any(source["provider"] == "web_search" for source in ctx["research_sources"])
            for ctx in seen_contexts
        )
        assert all(ctx.get("research_roles") for ctx in seen_contexts)

        refreshed = client.get(f"/api/research/deep/jobs/{job['job_id']}")
        assert refreshed.status_code == 200
        refreshed_job = refreshed.json()
        assert refreshed_job["status"] == "completed"
        assert refreshed_job["final_report"]
        assert "general" in refreshed_job["final_report"]
        assert "## 证据与来源" in refreshed_job["final_report"]
        assert "## 不确定性与缺口" in refreshed_job["final_report"]
        synthesis = [step for step in refreshed_job["steps"] if step["role_id"] == "synthesis"][0]
        assert synthesis["status"] == "completed"
    finally:
        orchestrator.shutdown(wait=False)


def test_start_endpoint_prefetches_evidence_pool_before_dispatch(tmp_path):
    seen_descriptions: list[str] = []
    seen_contexts: list[dict] = []

    class DummyPrefetcher:
        def prefetch(self, job):
            assert job.sources
            return [
                ResearchEvidence(
                    title="Prefetch NAS report",
                    url="https://example.com/nas-report",
                    source_kind="web",
                    quote_or_summary="NAS market prefetch summary",
                    claim=job.topic,
                    confidence=0.7,
                )
            ]

    def runner(description, *, subagent_name, context=None, cancel_event=None):
        seen_descriptions.append(description)
        seen_contexts.append(context or {})
        return "ok"

    orchestrator = ParallelAgentOrchestrator(max_concurrency=1, task_runner=runner)
    try:
        app = FastAPI()
        app.include_router(
            create_deep_research_router(
                orchestrator=orchestrator,
                agents_root=tmp_path / "agents",
                prefetcher=DummyPrefetcher(),
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/research/deep/start",
            json={
                "topic": "NAS market research",
                "lead_agent_name": "lead",
                "max_subagents": 1,
                "source_kinds": ["web"],
                "prefetch_sources": True,
            },
        )
        assert response.status_code == 200
        job = response.json()
        assert any(ev["url"] == "https://example.com/nas-report" for ev in job["evidence"])
        assert job["prefetch_logs"] == []

        for _ in range(100):
            batch = orchestrator.get_batch(job["dispatch_batch_id"])
            assert batch is not None
            if batch.status == "completed":
                break
            time.sleep(0.02)

        assert seen_descriptions
        assert "初始证据池" in seen_descriptions[0]
        assert "Prefetch NAS report" in seen_descriptions[0]
        assert seen_contexts
        assert any(
            ev["url"] == "https://example.com/nas-report"
            for ev in seen_contexts[0]["research_evidence"]
        )
        assert seen_contexts[0]["research_prefetch_logs"] == []
    finally:
        orchestrator.shutdown(wait=False)


def test_completed_research_writes_memory_to_lead_agent_only(tmp_path):
    def runner(description, *, subagent_name, context=None, cancel_event=None):
        return f"{subagent_name}: finding"

    agents_root = tmp_path / "agents"
    orchestrator = ParallelAgentOrchestrator(max_concurrency=4, task_runner=runner)
    try:
        app = FastAPI()
        app.include_router(
            create_deep_research_router(
                orchestrator=orchestrator,
                agents_root=agents_root,
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/research/deep/start",
            json={
                "topic": "NAS market research",
                "lead_agent_name": "lead",
                "max_subagents": 2,
            },
        )
        assert response.status_code == 200
        job = response.json()

        for _ in range(100):
            batch = orchestrator.get_batch(job["dispatch_batch_id"])
            assert batch is not None
            if batch.status == "completed":
                break
            time.sleep(0.02)

        refreshed = client.get(f"/api/research/deep/jobs/{job['job_id']}")
        assert refreshed.status_code == 200
        refreshed_job = refreshed.json()
        assert refreshed_job["memory_written_at"]
        memory_path = agents_root / "lead" / "agent-core" / "MEMORY.md"
        assert memory_path.exists()
        text = memory_path.read_text(encoding="utf-8")
        assert "NAS market research" in text
        assert job["job_id"] in text
        assert not any(agents_root.glob("virtual-research-*"))
    finally:
        orchestrator.shutdown(wait=False)


def test_cancelled_research_refreshes_job_status(tmp_path):
    def runner(description, *, subagent_name, context=None, cancel_event=None):
        for _ in range(50):
            if cancel_event is not None and cancel_event.is_set():
                return ""
            time.sleep(0.02)
        return "late finding"

    orchestrator = ParallelAgentOrchestrator(max_concurrency=1, task_runner=runner)
    try:
        app = FastAPI()
        app.include_router(
            create_deep_research_router(
                orchestrator=orchestrator,
                agents_root=tmp_path / "agents",
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/research/deep/start",
            json={
                "topic": "NAS market research",
                "lead_agent_name": "lead",
                "max_subagents": 2,
            },
        )
        assert response.status_code == 200
        job = response.json()
        batch = orchestrator.get_batch(job["dispatch_batch_id"])
        assert batch is not None
        for result in batch.results:
            orchestrator.cancel_task(result.task_id)

        for _ in range(100):
            batch = orchestrator.get_batch(job["dispatch_batch_id"])
            assert batch is not None
            if batch.status in ("cancelled", "partial"):
                break
            time.sleep(0.02)

        refreshed = client.get(f"/api/research/deep/jobs/{job['job_id']}")
        assert refreshed.status_code == 200
        refreshed_job = refreshed.json()
        assert refreshed_job["status"] == "cancelled"
        assert refreshed_job["final_report"] is None
        assert refreshed_job["memory_written_at"] is None
    finally:
        orchestrator.shutdown(wait=False)
