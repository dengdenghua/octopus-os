from pathlib import Path


def transform(source: str | Path) -> str:
    return Path(source).read_text(encoding="utf-8").upper()
