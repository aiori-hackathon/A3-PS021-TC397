"""One full evidence run, then RESULTS.md. Every step shares one RUN_ID.

    python scripts/run_all.py                 # suite + mutation + chaos
    python scripts/run_all.py --stability 20  # plus official/extra 20 times (~17 min)

Steps: TEST_MODE=fast pytest; DISABLE_DEDUP=1 pytest -m mutation; pytest -m slow;
optionally pytest -m "official or extra" --count=N; then build_results.py.
Exits non-zero if any step failed (RESULTS.md is still written, showing it).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stability", type=int, default=0, help="repeat official+extra N times")
    parser.add_argument("--run-id", default=time.strftime("%Y-%m-%d_%H-%M-%S"))
    args = parser.parse_args()
    base = {**os.environ, "RUN_ID": args.run_id, "TEST_MODE": "fast"}
    base.pop("DISABLE_DEDUP", None)
    pytest = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    steps = [
        ("suite", pytest, {}),
        ("mutation", [*pytest, "-m", "mutation"], {"DISABLE_DEDUP": "1"}),
        ("chaos", [*pytest, "-m", "slow"], {}),
    ]
    if args.stability:
        steps.append(
            ("stability", [*pytest, "-m", "official or extra", f"--count={args.stability}"], {})
        )
    failed = []
    for name, cmd, extra in steps:
        print(f"== {name}: {' '.join(cmd[2:])} {extra or ''}", flush=True)
        code = subprocess.run(cmd, cwd=ROOT, env={**base, **extra}, check=False).returncode
        if code != 0:
            failed.append(name)
    folder = ROOT / "results" / args.run_id
    subprocess.run([sys.executable, "scripts/build_results.py", str(folder)], cwd=ROOT, check=False)
    print(f"run {args.run_id}: " + (f"FAILED steps {failed}" if failed else "all steps passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
