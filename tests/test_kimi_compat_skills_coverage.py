"""Dense coverage for kimi_compat_skills (audit Q-05): data-source
parsers, media generation, and image asset helpers with mocked deps."""

from __future__ import annotations

from pathlib import Path

import pytest

import runtime.execution.suckers.kimi_compat_skills as kcs


class _FakeResp:
    def __init__(self, *, json=None, text="", content=b"", status=200):
        self._json = json
        self.text = text
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
        return

    def json(self):
        if callable(self._json):
            return self._json()
        return self._json


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _next(self):
        if not self._responses:
            raise AssertionError("no more mocked responses")
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("get", url))
        return self._next()

    def post(self, url, **kwargs):
        self.calls.append(("post", url))
        return self._next()


@pytest.fixture(autouse=True)
def _client_fixture(monkeypatch):
    monkeypatch.setattr(kcs, "HTTPX_AVAILABLE", True)
    monkeypatch.setenv("OPENAI_MEDIA_API_KEY", "test-key")


# ── data source descriptions ─────────────────────────────────


def test_data_source_desc_all_and_single() -> None:
    all_sources = kcs._get_data_source_desc()
    assert "sources" in all_sources
    assert "yahoo_finance" in all_sources["sources"]
    one = kcs._get_data_source_desc("arxiv")
    assert one["source"] == "arxiv"
    assert "params" in one
    unknown = kcs._get_data_source_desc("nope")
    assert "error" in unknown
    assert "yahoo_finance" in unknown["available"]


# ── yahoo finance ───────────────────────────────────────────


def test_yahoo_finance_parses_rows() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1, 2],
                    "indicators": {
                        "quote": [
                            {
                                "open": [10, 11],
                                "high": [12, 13],
                                "low": [9, 9.5],
                                "close": [11.5, 12.5],
                                "volume": [100, 200],
                            }
                        ]
                    },
                }
            ]
        }
    }
    client = _FakeClient([_FakeResp(json=payload)])
    out = kcs._yahoo_finance(client, "AAPL", "1mo", "1d")
    assert out["source"] == "yahoo_finance"
    assert len(out["rows"]) == 2
    assert out["rows"][0]["close"] == 11.5


def test_yahoo_finance_missing_symbol_and_error() -> None:
    assert "missing symbol" in kcs._yahoo_finance(_FakeClient([]), "", "1mo", "1d")["error"]
    out = kcs._yahoo_finance(_FakeClient([_FakeResp(status=500)]), "AAPL", "1mo", "1d")
    assert "yahoo_finance_error" in out["error"]
    no_data = kcs._yahoo_finance(_FakeClient([_FakeResp(json={"chart": {}})]), "AAPL", "1mo", "1d")
    assert "no data" in no_data["error"]


# ── arxiv / openalex / crossref ─────────────────────────────


def test_arxiv_search_parses_atom() -> None:
    xml = (
        "<feed><entry><title>  A Paper </title>"
        '<link href="http://x/1"/>'
        "<summary>  Summary here  </summary></entry></feed>"
    )
    client = _FakeClient([_FakeResp(text=xml)])
    out = kcs._arxiv_search(client, "graphs", 5)
    assert out["source"] == "arxiv"
    assert out["results"][0]["title"] == "A Paper"
    assert out["results"][0]["url"] == "http://x/1"
    assert "missing query" in kcs._arxiv_search(_FakeClient([]), "", 5)["error"]
    err = kcs._arxiv_search(_FakeClient([_FakeResp(status=500)]), "q", 5)
    assert "arxiv_error" in err["error"]


def test_openalex_search_parses_works() -> None:
    data = {
        "results": [
            {
                "title": "LLMs",
                "doi": "https://doi.org/1",
                "publication_year": 2024,
                "cited_by_count": 3,
            }
        ]
    }
    client = _FakeClient([_FakeResp(json=data)])
    out = kcs._openalex_search(client, "llm", 10)
    assert out["results"][0]["title"] == "LLMs"
    assert out["results"][0]["year"] == 2024
    assert "missing query" in kcs._openalex_search(_FakeClient([]), "", 10)["error"]
    err = kcs._openalex_search(_FakeClient([_FakeResp(status=500)]), "q", 10)
    assert "openalex_error" in err["error"]


def test_crossref_search_parses_items() -> None:
    data = {
        "message": {
            "items": [
                {
                    "title": ["Chips"],
                    "URL": "http://c/1",
                    "DOI": "10.1/x",
                    "published-print": {"date-parts": [[2023, 5]]},
                }
            ]
        }
    }
    client = _FakeClient([_FakeResp(json=data)])
    out = kcs._crossref_search(client, "ai chips", 10)
    assert out["results"][0]["title"] == "Chips"
    assert out["results"][0]["doi"] == "10.1/x"
    assert "missing query" in kcs._crossref_search(_FakeClient([]), "", 10)["error"]
    err = kcs._crossref_search(_FakeClient([_FakeResp(status=500)]), "q", 10)
    assert "crossref_error" in err["error"]


def test_get_data_source_routes(monkeypatch) -> None:
    monkeypatch.setattr(
        kcs, "_client", lambda **k: _FakeClient([_FakeResp(json={"chart": {"result": []}})])
    )

    def fake_yahoo(client, symbol, rng, interval):
        return {"source": "yahoo_finance", "symbol": symbol}

    monkeypatch.setattr(kcs, "_yahoo_finance", fake_yahoo)
    out = kcs._get_data_source("yahoo_finance", symbol="AAPL")
    assert out["source"] == "yahoo_finance"
    unknown = kcs._get_data_source("bogus")
    assert "error" in unknown


# ── media generation ────────────────────────────────────────


def test_generate_image_b64_and_url(monkeypatch, tmp_path: Path) -> None:
    import base64

    b64 = base64.b64encode(b"PNGDATA").decode()
    client = _FakeClient([_FakeResp(json={"data": [{"b64_json": b64}]})])
    monkeypatch.setattr(kcs, "_client", lambda **k: client)
    out = kcs._generate_image("a cat", output_path=str(tmp_path / "img.png"))
    assert out["ok"] is True
    assert (tmp_path / "img.png").read_bytes() == b"PNGDATA"

    client2 = _FakeClient([_FakeResp(json={"data": [{"url": "http://img/1"}]})])
    monkeypatch.setattr(kcs, "_client", lambda **k: client2)
    out2 = kcs._generate_image("a dog")
    assert out2["ok"] is True and out2["url"] == "http://img/1"


def test_generate_image_errors(monkeypatch) -> None:
    assert "missing prompt" in kcs._generate_image("  ")["error"]
    monkeypatch.setenv("OPENAI_MEDIA_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    missing = kcs._generate_image("x")
    assert "provider" in missing.get("error", "")
    monkeypatch.setenv("OPENAI_MEDIA_API_KEY", "k")
    client = _FakeClient([_FakeResp(status=500)])
    monkeypatch.setattr(kcs, "_client", lambda **k: client)
    err = kcs._generate_image("x")
    assert "generate_image_error" in err["error"]
    empty = _FakeClient([_FakeResp(json={})])
    monkeypatch.setattr(kcs, "_client", lambda **k: empty)
    out = kcs._generate_image("x")
    assert "generate_image_empty_response" in out["error"]


def test_generate_speech(monkeypatch, tmp_path: Path) -> None:
    assert "missing text" in kcs._generate_speech("  ")["error"]
    monkeypatch.setenv("OPENAI_MEDIA_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert "provider" in kcs._generate_speech("hi")["error"]
    monkeypatch.setenv("OPENAI_MEDIA_API_KEY", "k")
    client = _FakeClient([_FakeResp(content=b"AUDIO")])
    monkeypatch.setattr(kcs, "_client", lambda **k: client)
    out = kcs._generate_speech("hi", output_path=str(tmp_path / "a.mp3"))
    assert out["ok"] is True and out["bytes"] == 5
    client_err = _FakeClient([_FakeResp(status=500)])
    monkeypatch.setattr(kcs, "_client", lambda **k: client_err)
    assert "generate_speech_error" in kcs._generate_speech("hi")["error"]


def test_video_and_sfx_provider_missing() -> None:
    assert "missing prompt" in kcs._generate_video("")["error"]
    out = kcs._generate_video("go")
    assert "provider" in out.get("error", "")
    assert "missing prompt" in kcs._generate_sound_effects("")["error"]
    out2 = kcs._generate_sound_effects("boom")
    assert "provider" in out2.get("error", "")


# ── image asset helpers (real PIL) ──────────────────────────


def _make_image(tmp_path: Path, size=(8, 8)) -> Path:
    from PIL import Image

    img = Image.new("RGBA", size, (255, 255, 255, 255))
    for y in range(2, 5):
        for x in range(2, 5):
            img.putpixel((x, y), (0, 0, 0, 255))
    path = tmp_path / "img.png"
    img.save(path)
    return path


def test_load_image_paths(tmp_path: Path) -> None:
    p = _make_image(tmp_path)
    img, resolved, err = kcs._load_image(str(p))
    assert err is None and img is not None and resolved is not None
    missing = tmp_path / "nope.png"
    _, _, err2 = kcs._load_image(str(missing))
    assert "not a file" in err2
    _, _, err3 = kcs._load_image(str(tmp_path))
    assert err3 is not None


def test_find_asset_bbox_and_crop(tmp_path: Path) -> None:
    assert "missing image_path" in kcs._find_asset_bbox("")["error"]
    p = _make_image(tmp_path)
    out = kcs._find_asset_bbox(str(p), min_area=1)
    assert out["ok"] is True
    assert len(out["boxes"]) == 1
    box = out["boxes"][0]
    assert box["width"] == 3 and box["height"] == 3

    cropped = kcs._crop_and_replicate_assets_in_image(
        str(p), boxes=[box], output_dir=str(tmp_path / "assets")
    )
    assert cropped["ok"] is True
    assert len(cropped["assets"]) == 1
    assert Path(cropped["assets"][0]["path"]).exists()
    assert "missing image_path" in kcs._crop_and_replicate_assets_in_image("")["error"]


def test_safe_output_dir_and_media_path(tmp_path: Path, monkeypatch) -> None:
    import runtime.platform.process.paths as pp

    monkeypatch.setattr(pp, "app_paths", lambda: type("P", (), {"data_dir": tmp_path / "d"})())
    d = kcs._safe_output_dir(None, "media")
    assert d == tmp_path / "d" / "media"
    explicit = kcs._safe_output_dir(str(tmp_path / "x"), "media")
    assert explicit == tmp_path / "x"
    out = kcs._media_output_path("image", "png", output_path=str(tmp_path / "o.png"))
    assert out == tmp_path / "o.png"


# ── website version manager (list/snapshot/restore/delete) ───


def _project_dir(tmp_path: Path) -> Path:
    proj = tmp_path / "site"
    proj.mkdir()
    (proj / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (proj / "node_modules").mkdir()
    return proj


def test_website_version_manager_full_lifecycle(tmp_path: Path, monkeypatch) -> None:
    import runtime.platform.process.paths as pp

    monkeypatch.setattr(pp, "app_paths", lambda: type("P", (), {"data_dir": tmp_path / "d"})())
    proj = _project_dir(tmp_path)
    assert "missing project_dir" in kcs._website_version_manager(project_dir="")["error"]
    assert (
        "project_dir not found"
        in kcs._website_version_manager(project_dir=str(tmp_path / "nope"))["error"]
    )

    listed = kcs._website_version_manager("list", project_dir=str(proj))
    assert listed["ok"] is True and listed["versions"] == []

    snap = kcs._website_version_manager("snapshot", project_dir=str(proj), label="v1")
    assert snap["ok"] is True
    vid = snap["version"]["id"]
    assert (kcs._version_root(proj) / vid / "index.html").exists()

    listed2 = kcs._website_version_manager("list", project_dir=str(proj))
    assert len(listed2["versions"]) == 1

    # mutate the project then restore
    (proj / "index.html").write_text("<h1>changed</h1>", encoding="utf-8")
    restored = kcs._website_version_manager("restore", project_dir=str(proj), version_id=vid)
    assert restored["ok"] is True
    assert "<h1>hi</h1>" in (proj / "index.html").read_text(encoding="utf-8")

    deleted = kcs._website_version_manager("delete", project_dir=str(proj), version_id=vid)
    assert deleted["ok"] is True
    listed3 = kcs._website_version_manager("list", project_dir=str(proj))
    assert listed3["versions"] == []

    assert (
        "missing version_id"
        in kcs._website_version_manager("restore", project_dir=str(proj))["error"]
    )
    assert (
        "version not found"
        in kcs._website_version_manager("restore", project_dir=str(proj), version_id="nope")[
            "error"
        ]
    )
    assert "unknown action" in kcs._website_version_manager("bogus", project_dir=str(proj))["error"]


def test_screenshot_web_full_page(monkeypatch) -> None:
    captured: dict = {}

    def fake_browser_screenshot(**kw):
        captured.update(kw)
        return {"ok": True, "path": kw.get("path")}

    import runtime.execution.suckers.browser_skills as bs

    monkeypatch.setattr(bs, "_browser_screenshot", fake_browser_screenshot)
    out = kcs._screenshot_web_full_page(url="http://x", path="/tmp/out.png")
    assert out["ok"] is True
    assert captured["full_page"] is True
    out2 = kcs._screenshot_web_full_page(url="http://x")
    assert out2["path"]  # default path generated


def test_register_kimi_compat_skills() -> None:
    from runtime.execution.suckers.registry import SkillRegistry

    reg = SkillRegistry()
    n = kcs.register_kimi_compat_skills(reg)
    assert n >= 10
    assert "generate_image" in reg.all_names()
    assert "deploy_website" in reg.all_names()

