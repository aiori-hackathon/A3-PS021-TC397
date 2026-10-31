"""A test-code check ruff does not make: no local variable shadows an imported name.

Regression: in S1 a local list called `duplicates` shadowed the imported helper
`duplicates()`, so the test failed with "'list' object is not callable".
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_local_shadows_an_import():
    hits = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                    targets = [node.target]
                else:
                    continue
                hits += [
                    f"{path.relative_to(ROOT)}:{node.lineno} {fn.name}() shadows {t.id}"
                    for t in targets
                    if isinstance(t, ast.Name) and t.id in imported
                ]
    assert not hits, hits
