"""Wording rules from CLAUDE.md, checked over code, UI and tests (docs excluded)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ["bridge", "adapters", "services", "dashboard", "scripts", "tests", "supervisor.py"]
SUFFIXES = {".py", ".js", ".html", ".css", ".json"}

# Built by joining so this file does not contain the phrases itself.
FORBIDDEN = [
    " ".join(["IETF", "standard"]),
    " ".join(["implements", "MCP"]),
    " ".join(["implements", "A2A"]),
    "-".join(["exactly", "once"]),
    " ".join(["exactly", "once"]),
    " ".join(["AI", "agent"]),
]


def files():
    for name in SCANNED:
        path = ROOT / name
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (p for p in path.rglob("*") if p.suffix in SUFFIXES)


def test_no_forbidden_phrases():
    hits = []
    for path in files():
        text = path.read_text(encoding="utf-8").lower()
        hits += [f"{path.relative_to(ROOT)}: {p}" for p in FORBIDDEN if p.lower() in text]
    assert not hits, hits
