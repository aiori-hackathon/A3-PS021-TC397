"""Check every relative link and #anchor in the Markdown docs.

    python scripts/check_links.py          # README.md, CLAUDE.md, CONTRIBUTING.md, docs/*.md

Exits 1 and lists each broken link. Links to http(s), mailto and links inside code
are skipped; anchors follow GitHub's heading rules. Run by CI so a submission never
ships with a dead link.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = heading.strip().lower().replace("`", "")
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _prose_lines(path: Path):
    """Lines outside fenced code blocks."""
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            yield line


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out: set[str] = set()
    for line in _prose_lines(path):
        m = HEADING.match(line)
        if not m:
            continue
        base = slug(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out


def check(files: list[Path], root: Path) -> list[tuple[Path, int, str]]:
    problems: list[tuple[Path, int, str]] = []
    cache: dict[Path, set[str]] = {}
    for path in files:
        fenced = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            for target in LINK.findall(INLINE_CODE.sub("", line)):
                if re.match(r"^(https?:|mailto:)", target):
                    continue
                name, _, anchor = target.partition("#")
                dest = (path.parent / name).resolve() if name else path
                shown = name or path.name
                if not dest.exists():
                    problems.append((path, number, f"no such file {shown}"))
                elif anchor and dest.suffix == ".md":
                    found = cache.setdefault(dest, anchors(dest))
                    if anchor.lower() not in found:
                        problems.append((path, number, f"{shown} has no heading #{anchor}"))
    return problems


def main() -> int:
    files = [ROOT / n for n in ("README.md", "CLAUDE.md", "CONTRIBUTING.md")]
    files += sorted((ROOT / "docs").glob("*.md"))
    files = [f for f in files if f.exists()]
    problems = check(files, ROOT)
    for path, number, why in problems:
        print(f"{path.relative_to(ROOT)}:{number}: {why}")
    print(
        f"{len(files)} files checked: "
        + (f"{len(problems)} broken link(s)" if problems else "all links resolve")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
