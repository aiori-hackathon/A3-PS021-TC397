"""FastAPI app for the Dialog Bridge: routes, WebSocket, dashboard. See docs/BRIDGE.md.

Phase 0 skeleton: only /health. The event log arrives in Phase 2, so
last_seq is 0 until then.
"""

import uuid

from fastapi import FastAPI

BOOT_ID = uuid.uuid4().hex[:12]

app = FastAPI(title="Dialog Bridge (dialog-state/0.1-experimental)")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "boot_id": BOOT_ID, "last_seq": 0}
