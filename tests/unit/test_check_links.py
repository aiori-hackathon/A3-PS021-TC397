"""U: the relative-link and anchor checker (scripts/check_links.py)."""

from scripts import check_links


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_github_heading_anchors():
    slug = check_links.slug
    assert slug("X1 — Uncertain side-effect outcome") == "x1--uncertain-side-effect-outcome"
    assert slug("Leases and fences") == "leases-and-fences"
    assert slug("`task.hold` and `task.release`") == "taskhold-and-taskrelease"
    assert slug("What these results do and do not show") == "what-these-results-do-and-do-not-show"


def test_anchors_ignore_code_blocks_and_number_repeats(tmp_path):
    write(tmp_path, "a.md", "# One\n```\n# not a heading\n```\n## Two\n## Two\n")
    assert check_links.anchors(tmp_path / "a.md") == {"one", "two", "two-1"}


def test_good_links_pass(tmp_path):
    write(
        tmp_path,
        "README.md",
        "[doc](docs/A.md#leases-and-fences) [self](#top) [web](https://x.y)\n# Top\n",
    )
    write(tmp_path, "docs/A.md", "## Leases and fences\n[back](../README.md)\n")
    assert check_links.check([tmp_path / "README.md", tmp_path / "docs/A.md"], tmp_path) == []


def test_missing_file_and_missing_anchor_are_reported(tmp_path):
    write(tmp_path, "README.md", "[a](docs/NOPE.md) [b](docs/A.md#missing) ![i](img/x.png)\n")
    write(tmp_path, "docs/A.md", "# Real\n")
    found = check_links.check([tmp_path / "README.md"], tmp_path)
    assert [(p.name, why) for p, _, why in found] == [
        ("README.md", "no such file docs/NOPE.md"),
        ("README.md", "docs/A.md has no heading #missing"),
        ("README.md", "no such file img/x.png"),
    ]


def test_links_inside_code_are_not_checked(tmp_path):
    write(tmp_path, "README.md", "`[x](nowhere.md)`\n```\n[y](also-nowhere.md)\n```\n")
    assert check_links.check([tmp_path / "README.md"], tmp_path) == []
