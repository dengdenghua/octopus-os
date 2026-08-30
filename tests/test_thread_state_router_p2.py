"""Tests for Echo Native Session API v2 endpoints."""

from __future__ import annotations

import tempfile

import pytest

from runtime.memory.threads.store import ThreadStateStore
from runtime.sensing.gateway.thread_state_router import create_thread_state_router

try:
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    TestClient = None  # type: ignore[assignment, misc]

pytestmark = pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")


@pytest.fixture
def _p2_env():
    """Shared store + FastAPI client for the feedback / stats endpoints.

    ``client`` and ``sample_thread`` both depend on this fixture so they share
    the same store instance within a test.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = ThreadStateStore(
            per_agent_base=tmpdir,
            search_enabled=True,
            feedback_enabled=True,
        )
        from fastapi import FastAPI

        router = create_thread_state_router(store=store, require_auth=False)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        yield store, client


@pytest.fixture
def client(_p2_env):
    return _p2_env[1]


@pytest.fixture
def sample_thread(_p2_env):
    """A thread with a few messages so message_index references stay valid."""
    store, _ = _p2_env
    thread = store.create(
        metadata={},
        values={
            "messages": [
                {"role": "user", "content": "How do I fix authentication bug?"},
                {"role": "assistant", "content": "Check your JWT configuration."},
                {"role": "user", "content": "Thanks, that helped!"},
            ]
        },
    )
    return thread["thread_id"]


class TestFullTextSearch:
    """Test /api/threads/fts endpoint."""

    @pytest.fixture
    def setup(self):
        """Setup store, client, and sample thread."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ThreadStateStore(
                per_agent_base=tmpdir,
                search_enabled=True,
                feedback_enabled=True,
            )
            from fastapi import FastAPI

            router = create_thread_state_router(store=store, require_auth=False)
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            # Create sample thread
            thread = store.create(
                metadata={},
                values={
                    "messages": [
                        {"role": "user", "content": "How do I fix authentication bug?"},
                        {
                            "role": "assistant",
                            "content": "Check your JWT configuration.",
                        },
                        {"role": "user", "content": "Thanks, that helped!"},
                    ]
                },
            )
            thread_id = thread["thread_id"]

            yield client, thread_id

    def test_search_basic(self, setup):
        """Test basic full-text search."""
        client, _ = setup
        response = client.get("/api/threads/fts?q=authentication")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "count" in data
        assert data["count"] >= 0

    def test_search_with_results(self, setup):
        """Test search returns matching thread."""
        client, thread_id = setup
        response = client.get("/api/threads/fts?q=authentication")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
        result = data["results"][0]
        assert result["thread_id"] == thread_id
        assert "snippet" in result
        assert "authentication" in result["snippet"].lower()

    def test_search_empty_query(self, setup):
        """Test search with empty query returns 400."""
        client, _ = setup
        response = client.get("/api/threads/fts?q=")
        assert response.status_code == 400

    def test_search_with_filters(self, setup):
        """Test search with agent_id filter."""
        client, _ = setup
        response = client.get("/api/threads/fts?q=authentication&agent_id=test_agent")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data

    def test_search_with_date_range(self, setup):
        """Test search with date range filters."""
        client, _ = setup
        response = client.get("/api/threads/fts?q=authentication&after=2020-01-01T00:00:00Z")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data

    def test_search_limit(self, setup):
        """Test search respects limit parameter."""
        client, _ = setup
        response = client.get("/api/threads/fts?q=authentication&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) <= 5

    def test_search_limit_bounds(self, setup):
        """Test search limit validation."""
        client, _ = setup
        response = client.get("/api/threads/fts?q=test&limit=0")
        assert response.status_code == 422  # Validation error

        response = client.get("/api/threads/fts?q=test&limit=200")
        assert response.status_code == 422  # Validation error


class TestExportMarkdown:
    """Test /api/threads/{thread_id}/export endpoint."""

    @pytest.fixture
    def setup(self):
        """Setup store, client, and sample thread."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ThreadStateStore(
                per_agent_base=tmpdir,
                search_enabled=True,
                feedback_enabled=True,
            )
            from fastapi import FastAPI

            router = create_thread_state_router(store=store, require_auth=False)
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            thread = store.create(
                metadata={},
                values={
                    "messages": [
                        {"role": "user", "content": "How do I fix authentication bug?"},
                        {
                            "role": "assistant",
                            "content": "Check your JWT configuration.",
                        },
                    ]
                },
            )
            thread_id = thread["thread_id"]

            yield client, thread_id

    def test_export_basic(self, setup):
        """Test basic markdown export."""
        client, thread_id = setup
        response = client.get(f"/api/threads/{thread_id}/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert "attachment" in response.headers["content-disposition"]

    def test_export_content(self, setup):
        """Test exported markdown contains expected content."""
        client, thread_id = setup
        response = client.get(f"/api/threads/{thread_id}/export")
        assert response.status_code == 200
        content = response.text
        assert "---" in content  # YAML frontmatter
        assert "thread_id:" in content
        # Export headings carry a per-message index prefix (## Message N: Role).
        assert "User" in content and "Assistant" in content
        assert "authentication" in content.lower()

    def test_export_nonexistent_thread(self, setup):
        """Test export of nonexistent thread returns 404."""
        client, _ = setup
        response = client.get("/api/threads/nonexistent_thread_id/export")
        assert response.status_code == 404

    def test_export_invalid_thread_id(self, setup):
        """Test export with invalid thread_id returns 400."""
        client, _ = setup
        response = client.get("/api/threads/invalid<>id/export")
        assert response.status_code == 400


class TestAddFeedback:
    """Test POST /api/threads/{thread_id}/feedback endpoint."""

    def test_add_thumbs_up(self, client, sample_thread):
        """Test adding thumbs up feedback."""
        response = client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={
                "message_index": 1,
                "feedback_type": "thumbs_up",
                "tags": ["helpful"],
                "comment": "Great answer!",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["thread_id"] == sample_thread
        assert data["message_index"] == 1
        assert data["feedback_type"] == "thumbs_up"
        assert data["tags"] == ["helpful"]
        assert data["comment"] == "Great answer!"
        assert "timestamp" in data

    def test_add_thumbs_down(self, client, sample_thread):
        """Test adding thumbs down feedback."""
        response = client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={
                "message_index": 1,
                "feedback_type": "thumbs_down",
                "tags": ["inaccurate"],
                "comment": "Wrong information",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["feedback_type"] == "thumbs_down"

    def test_add_feedback_no_tags(self, client, sample_thread):
        """Test adding feedback without tags."""
        response = client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 0, "feedback_type": "thumbs_up"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tags"] == []
        assert data["comment"] == ""

    def test_add_feedback_invalid_type(self, client, sample_thread):
        """Test adding feedback with invalid type."""
        response = client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 0, "feedback_type": "invalid"},
        )
        assert response.status_code == 400

    def test_add_feedback_invalid_index(self, client, sample_thread):
        """Test adding feedback with invalid message_index."""
        response = client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": -1, "feedback_type": "thumbs_up"},
        )
        assert response.status_code == 400

    def test_add_feedback_nonexistent_thread(self, client):
        """Test adding feedback to nonexistent thread."""
        response = client.post(
            "/api/threads/nonexistent/feedback",
            json={"message_index": 0, "feedback_type": "thumbs_up"},
        )
        assert response.status_code == 404


class TestGetFeedback:
    """Test GET /api/threads/{thread_id}/feedback endpoint."""

    def test_get_all_feedback(self, client, sample_thread):
        """Test getting all feedback for a thread."""
        # Add some feedback first
        client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 0, "feedback_type": "thumbs_up"},
        )
        client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 1, "feedback_type": "thumbs_down"},
        )

        response = client.get(f"/api/threads/{sample_thread}/feedback")
        assert response.status_code == 200
        data = response.json()
        assert "feedbacks" in data
        assert len(data["feedbacks"]) == 2

    def test_get_feedback_for_message(self, client, sample_thread):
        """Test getting feedback for specific message."""
        client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 1, "feedback_type": "thumbs_up"},
        )

        response = client.get(f"/api/threads/{sample_thread}/feedback?message_index=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data["feedbacks"]) >= 1
        assert all(f["message_index"] == 1 for f in data["feedbacks"])

    def test_get_feedback_empty(self, client, sample_thread):
        """Test getting feedback when none exists."""
        response = client.get(f"/api/threads/{sample_thread}/feedback")
        assert response.status_code == 200
        data = response.json()
        assert data["feedbacks"] == []

    def test_get_feedback_nonexistent_thread(self, client):
        """Test getting feedback for nonexistent thread."""
        response = client.get("/api/threads/nonexistent/feedback")
        assert response.status_code == 404


class TestGetFeedbackStats:
    """Test GET /api/threads/{thread_id}/feedback/stats endpoint."""

    def test_get_stats(self, client, sample_thread):
        """Test getting feedback stats."""
        # Add feedback
        client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 0, "feedback_type": "thumbs_up"},
        )
        client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 1, "feedback_type": "thumbs_up"},
        )
        client.post(
            f"/api/threads/{sample_thread}/feedback",
            json={"message_index": 1, "feedback_type": "thumbs_down"},
        )

        response = client.get(f"/api/threads/{sample_thread}/feedback/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "thumbs_up" in data
        assert "thumbs_down" in data
        assert data["total"] == 3
        assert data["thumbs_up"] == 2
        assert data["thumbs_down"] == 1

    def test_get_stats_empty(self, client, sample_thread):
        """Test getting stats when no feedback exists."""
        response = client.get(f"/api/threads/{sample_thread}/feedback/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_get_stats_nonexistent_thread(self, client):
        """Test getting stats for nonexistent thread."""
        response = client.get("/api/threads/nonexistent/feedback/stats")
        assert response.status_code == 404


class TestP2FeaturesDisabled:
    """Test behavior when P2 features are disabled."""

    @pytest.fixture
    def client_no_p2(self):
        """Create client with P2 features disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ThreadStateStore(
                per_agent_base=tmpdir,
                search_enabled=False,
                feedback_enabled=False,
            )
            router = create_thread_state_router(store=store, require_auth=False)
            from fastapi import FastAPI

            app = FastAPI()
            app.include_router(router)
            yield TestClient(app)

    def test_search_disabled(self, client_no_p2):
        """Test search returns 501 when disabled."""
        response = client_no_p2.get("/api/threads/fts?q=test")
        assert response.status_code == 501

    def test_feedback_disabled(self, client_no_p2):
        """Test feedback returns 501 when disabled."""
        # Create a thread first
        create_resp = client_no_p2.post("/api/threads", json={})
        thread_id = create_resp.json()["thread_id"]

        response = client_no_p2.post(
            f"/api/threads/{thread_id}/feedback",
            json={"message_index": 0, "feedback_type": "thumbs_up"},
        )
        assert response.status_code == 501

