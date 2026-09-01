"""Dense coverage for image-search provider backends (audit Q-05)."""

from __future__ import annotations

import runtime.execution.suckers.image_search_backends as isb


def test_unwrap_ddg_url() -> None:
    assert _unwrap("https://example.com/x?a=1") == "https://example.com/x?a=1"
    assert (
        _unwrap("https://duckduckgo.com/?q=x&uddg=https%3A%2F%2Ftarget.com%2Fimg")
        == "https://target.com/img"
    )
    assert _unwrap("https://duckduckgo.com/?q=x") == "https://duckduckgo.com/?q=x"


def _unwrap(url: str) -> str:
    return isb._unwrap_ddg_url(url)


class _Client:
    def __init__(self, handlers=None):
        self.handlers = handlers or {}
        self.calls = []

    @staticmethod
    def _invoke(handler):
        # A callable handler stands for an exception-raising stub.
        return handler() if callable(handler) else handler

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        return self._invoke(self.handlers.get(("get", url), _resp200({})))

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        return self._invoke(self.handlers.get(("post", url), _resp200({})))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Resp:
    url = "https://duckduckgo.com/"

    def __init__(self, data=None, text="", status=200):
        self._data = data
        self.text = text
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _resp200(data):
    return _Resp(data=data)


def test_search_image_by_text_validation(monkeypatch) -> None:
    assert isb.search_image_by_text("  ")["error"] == "missing query"
    monkeypatch.setattr(isb, "HTTPX_AVAILABLE", False)
    assert isb.search_image_by_text("cat")["error"] == "httpx not installed"


def test_search_by_text_falls_back_to_ddg(monkeypatch) -> None:
    client = _Client(
        {
            ("get", "https://duckduckgo.com/"): _Resp(text='vqd="abc123"'),
            ("get", "https://duckduckgo.com/i.js"): _Resp(
                data={
                    "results": [
                        {
                            "title": "A &amp; B",
                            "image": "https://i/a.png",
                            "thumbnail": "https://i/a_t.png",
                            "url": "https://src/a",
                            "width": 10,
                            "height": 20,
                        }
                    ]
                }
            ),
        }
    )
    monkeypatch.setattr(isb, "_client", lambda **kw: client)
    monkeypatch.delenv("IMAGE_SEARCH_BACKEND", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    out = isb.search_image_by_text("cats", max_results=5)
    assert out["backend"] == "ddg"
    assert out["results"][0]["title"] == "A & B"
    assert out["results"][0]["image_url"] == "https://i/a.png"


def test_search_by_text_explicit_backend(monkeypatch) -> None:
    client = _Client(
        {
            ("get", "https://duckduckgo.com/"): _Resp(text='vqd="v"'),
            ("get", "https://duckduckgo.com/i.js"): _Resp(data={"results": []}),
        }
    )
    monkeypatch.setattr(isb, "_client", lambda **kw: client)
    monkeypatch.setenv("IMAGE_SEARCH_BACKEND", "brave")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    out = isb.search_image_by_text("cats")
    # Explicit backend that errors falls through to the keyless ddg fallback.
    assert out["backend"] == "ddg"


def test_brave_image_search(monkeypatch) -> None:
    client = _Client(
        {
            ("get", "https://api.search.brave.com/res/v1/images/search"): _Resp(
                data={
                    "results": [
                        {
                            "title": "T",
                            "url": "https://src",
                            "properties": {"url": "https://i.png", "width": 1, "height": 2},
                            "thumbnail": {"src": "https://t.png"},
                        }
                    ]
                }
            )
        }
    )
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    out = isb._brave_image_search(client, "q", 5)
    assert out["backend"] == "brave"
    assert out["results"][0]["image_url"] == "https://i.png"

    monkeypatch.delenv("BRAVE_API_KEY")
    assert isb._brave_image_search(client, "q", 5)["error"] == "brave_missing_key"

    def _boom(*a, **kw):
        raise OSError("down")

    monkeypatch.setenv("BRAVE_API_KEY", "k")
    bad = _Client({("get", "https://api.search.brave.com/res/v1/images/search"): _boom})
    out = isb._brave_image_search(bad, "q", 5)
    assert out["error"].startswith("brave_error:")


def test_serper_image_search(monkeypatch) -> None:
    client = _Client(
        {
            ("post", "https://google.serper.dev/images"): _Resp(
                data={
                    "images": [
                        {
                            "title": "T",
                            "imageUrl": "https://i",
                            "thumbnailUrl": "https://t",
                            "link": "https://s",
                            "imageWidth": 3,
                            "imageHeight": 4,
                        }
                    ]
                }
            )
        }
    )
    monkeypatch.setenv("SERPER_API_KEY", "k")
    out = isb._serper_image_search(client, "q", 5)
    assert out["backend"] == "serper"
    assert out["results"][0]["image_url"] == "https://i"
    monkeypatch.delenv("SERPER_API_KEY")
    assert isb._serper_image_search(client, "q", 5)["error"] == "serper_missing_key"


def test_searxng_image_search(monkeypatch) -> None:
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    client = _Client()
    assert isb._searxng_image_search(client, "q", 5)["error"] == "searxng_missing_url"

    monkeypatch.setenv("SEARXNG_URL", "https://sx.example/")
    client = _Client(
        {
            ("get", "https://sx.example/search"): _Resp(
                data={
                    "results": [
                        {
                            "title": "T",
                            "img_src": "https://i",
                            "thumbnail": "https://t",
                            "url": "https://s",
                        }
                    ]
                }
            )
        }
    )
    out = isb._searxng_image_search(client, "q", 5)
    assert out["backend"] == "searxng"
    assert out["results"][0]["thumbnail_url"] == "https://t"

    def _boom(*a, **kw):
        raise RuntimeError("bad gateway")

    bad = _Client({("get", "https://sx.example/search"): _boom})
    assert isb._searxng_image_search(bad, "q", 5)["error"].startswith("searxng_error:")


def test_ddg_image_search_vqd_and_fallback(monkeypatch) -> None:
    # vqd found
    client = _Client(
        {
            ("get", "https://duckduckgo.com/"): _Resp(text="vqd='xyz'"),
            ("get", "https://duckduckgo.com/i.js"): _Resp(data={"results": []}),
        }
    )
    out = isb._ddg_image_search(client, "q", 5)
    assert out["backend"] == "ddg"

    # no vqd -> html fallback
    client2 = _Client(
        {
            ("get", "https://duckduckgo.com/"): _Resp(text="no vqd here"),
            ("post", "https://html.duckduckgo.com/html/"): _Resp(
                text='<a class="result__a" href="https://duckduckgo.com/?uddg=https%3A%2F%2Fx.com%2F1">Title &amp; More</a>'
            ),
        }
    )
    out = isb._ddg_image_search(client2, "q", 5)
    assert out["backend"] == "ddg-html"
    assert out["results"][0]["source_url"] == "https://x.com/1"
    assert out["results"][0]["title"] == "Title & More"

    # get raises -> html fallback; html raises -> error
    def _boom(*a, **kw):
        raise OSError("net")

    client3 = _Client(
        {
            ("get", "https://duckduckgo.com/"): _boom,
            ("post", "https://html.duckduckgo.com/html/"): _boom,
        }
    )
    out = isb._ddg_image_search(client3, "q", 5)
    assert out["error"].startswith("ddg_error:")


def test_search_image_by_image() -> None:
    assert isb.search_image_by_image()["error"] == "missing image_url or image_path"
    out = isb.search_image_by_image(image_url="https://i/x.png")
    assert out["image_url"] == "https://i/x.png"
    assert "not_configured" in out["error"]

