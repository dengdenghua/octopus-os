from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

from runtime.platform.credentials import get_secret

from ..models import ReachItem

_REPO_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/#?]+)(?:/(issues|pull)/(\d+))?")


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = get_secret("reach.github_token", env_var="GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def search_github(client: Any, query: str, limit: int) -> dict[str, Any]:
    response = client.get(
        "https://api.github.com/search/repositories",
        params={"q": query, "per_page": limit},
        headers=_headers(),
    )
    response.raise_for_status()
    items = [
        ReachItem(
            title=row.get("full_name") or row.get("name") or "GitHub repository",
            url=row.get("html_url") or "",
            snippet=row.get("description") or "",
            platform="github",
            kind="repository",
            metadata={
                "stars": row.get("stargazers_count", 0),
                "language": row.get("language"),
                "updated_at": row.get("updated_at"),
            },
        ).to_dict()
        for row in response.json().get("items", [])[:limit]
    ]
    return {"ok": True, "platform": "github", "backend": "github_api", "results": items}


def read_github(client: Any, url: str) -> dict[str, Any] | None:
    match = _REPO_RE.match(url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    item_kind, item_number = match.group(3), match.group(4)
    api = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
    headers = _headers()
    if item_kind and item_number:
        endpoint = "pulls" if item_kind == "pull" else "issues"
        response = client.get(f"{api}/{endpoint}/{item_number}", headers=headers)
        response.raise_for_status()
        data = response.json()
        comments_response = client.get(f"{api}/issues/{item_number}/comments", headers=headers)
        comments = comments_response.json() if comments_response.status_code == 200 else []
        return {
            "ok": True,
            "platform": "github",
            "backend": "github_api",
            "kind": "pull_request" if item_kind == "pull" else "issue",
            "url": data.get("html_url") or url,
            "title": data.get("title") or "",
            "body": (data.get("body") or "")[:40_000],
            "state": data.get("state"),
            "author": (data.get("user") or {}).get("login"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "comments": [
                {
                    "author": (comment.get("user") or {}).get("login"),
                    "body": (comment.get("body") or "")[:10_000],
                    "created_at": comment.get("created_at"),
                }
                for comment in comments[:50]
            ],
            "comments_truncated": len(comments) > 50,
        }
    response = client.get(api, headers=headers)
    response.raise_for_status()
    data = response.json()
    readme_text = ""
    readme = client.get(f"{api}/readme", headers=headers)
    if readme.status_code == 200:
        payload = readme.json()
        try:
            readme_text = base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")
        except (ValueError, TypeError):
            readme_text = ""
    return {
        "ok": True,
        "platform": "github",
        "backend": "github_api",
        "url": data.get("html_url") or url,
        "title": data.get("full_name") or f"{owner}/{repo}",
        "description": data.get("description") or "",
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "language": data.get("language"),
        "topics": data.get("topics", []),
        "readme": readme_text[:40_000],
        "truncated": len(readme_text) > 40_000,
    }
