"""wait_until: the only way tests wait. See docs/TESTING.md#helpers."""

import os
import time

TIMEOUT_FACTOR = 5 if os.environ.get("TEST_MODE", "fast") == "watch" else 1


def wait_until(check, timeout=10, every=0.05):
    end = time.monotonic() + timeout * TIMEOUT_FACTOR
    last = None
    while time.monotonic() < end:
        last = check()
        if last:
            return last
        time.sleep(every)
    raise TimeoutError(f"gave up after {timeout}s, last value: {last!r}")
