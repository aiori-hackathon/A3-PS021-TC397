"""BridgeError: a request the bridge refuses, mapped to an error reply (docs/SCHEMA.md#3-replies-and-errors)."""

from __future__ import annotations

from typing import Any

from bridge.models import Reply


class BridgeError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def reply(self) -> Reply:
        return Reply.failure(self.code, self.message, self.details)
