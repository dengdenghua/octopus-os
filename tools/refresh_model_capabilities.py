"""Regenerate ``data/model_capabilities.json`` from models.dev.

models.dev is a community-maintained database of per-MODEL capabilities. Our
own compat profiles are per-PROVIDER, which is the right granularity for
request quirks that a whole vendor shares but the wrong one for facts that
differ between a vendor's own models — ``kimi-k3`` rejects ``temperature``
while its siblings accept it, and a relay's ``deepseek-v4-flash`` has a 1M
window where our hand-written entry said 128k.

The snapshot is vendored rather than fetched at runtime so startup makes no
network call, offline installs behave identically, and CI is deterministic.
Run this to refresh it:

    make refresh-model-capabilities

Only the three fields we act on are kept, so the snapshot stays small and
reviewable in a diff:

``context``       input window, corrects an entry's ``context_window``
``temperature``   False means the model 400s on a temperature parameter
``reasoning``     True means it spends output tokens thinking before writing

Usage:
    python -m tools.refresh_model_capabilities [--url URL] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_URL = "https://models.dev/api.json"
# Lives under resources/, not data/: data/ is gitignored operator state, while
# this is a bundled read-only asset that must ship with the repo. See
# runtime.platform.process.paths.resources_root.
DEFAULT_OUT = Path("resources/models/capabilities.json")

# A bare "python-httpx/x.y" gets rejected by bot-protection layers; see
# runtime.sensing.model_router.models.DEFAULT_USER_AGENT for the full story.
_USER_AGENT = "echo-agent-capability-refresh"


def _fetch(url: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(url, timeout=120.0, headers={"User-Agent": _USER_AGENT})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit(f"unexpected payload shape from {url}: {type(payload).__name__}")
    return payload


def distill(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reduce the upstream dump to the fields we actually act on.

    A model id can appear under several providers (a relay and the vendor
    itself). Their capability claims agree in practice; when they disagree we
    keep the first and do not try to arbitrate — the operator's own
    ``custom_models.json`` entry always wins over this snapshot anyway.
    """
    out: dict[str, dict[str, Any]] = {}
    for provider in raw.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, model in models.items():
            if not isinstance(model, dict) or model_id in out:
                continue
            record: dict[str, Any] = {}

            limit = model.get("limit")
            if isinstance(limit, dict):
                context = limit.get("context")
                if isinstance(context, int) and context > 0:
                    record["context"] = context

            # Only ``False`` is worth recording: it is the actionable claim
            # ("omit this parameter"). ``True`` is the default assumption.
            if model.get("temperature") is False:
                record["temperature"] = False

            # Reasoning models spend max_tokens on thinking before they write,
            # so an output budget that only covers the thinking returns HTTP
            # 200 with empty content. Recording this lets the router raise its
            # floor for models the operator never declared.
            if model.get("reasoning") is True:
                record["reasoning"] = True

            # ``interleaved.field`` (which response key carries reasoning) is
            # deliberately NOT captured. Across the whole upstream dump it only
            # ever names ``reasoning_content`` (211 models) or
            # ``reasoning_details`` (2), and extract_openai_compat_reasoning
            # already reads both — recording it would be dead weight in the
            # snapshot and dead code in the parser. Revisit if upstream starts
            # declaring a key we do not handle.

            if record:
                out[model_id] = record
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    raw = _fetch(args.url)
    models = distill(raw)
    if not models:
        raise SystemExit("refusing to write an empty snapshot")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"source": args.url, "models": models},
            indent=1,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out} · {len(models)} models from {len(raw)} providers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
