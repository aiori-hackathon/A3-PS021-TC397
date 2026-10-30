"""Codecs between native mock formats and the shared envelope. See docs/PROTOCOLS.md.

Each codec module exposes the same pure functions (no I/O):

    encode_request(env) -> native        decode_request(native) -> Envelope
    encode_reply(reply, native_req)      decode_reply(native) -> Reply
    encode_task_view(task) -> native     decode_task_view(native) -> Task

The bridge uses decode_request / encode_reply / encode_task_view; the mock
adapters use encode_request / decode_reply. Both sides share one module, so
the formats cannot drift apart.
"""


class CodecError(ValueError):
    """The native message does not have the expected shape (maps to validation_error)."""


ENVELOPE_ID_FIELDS = ("dialog_id", "task_id", "fence")


def is_task_record(value: object) -> bool:
    return isinstance(value, dict) and {"task_id", "state", "steps"} <= value.keys()
