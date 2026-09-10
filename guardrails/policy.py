"""Small, explicit demo rules; no model calls or external data services."""

import json
import math
import re
import unicodedata


MAX_MESSAGE_CHARS = 280
MAX_NUMBER = 1000
MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 65536
TOOLS = {"hello": "hello_world", "math": "add_numbers", "time": "utc_time"}
SECRET = re.compile(
    r"\b(?:password|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+"
    r"|\bsk-[a-z0-9_-]{12,}\b"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.IGNORECASE,
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)


class Denied(ValueError):
    """A fixed, payload-free explanation that is safe to send to a client."""

    def __init__(self, rule: str, message: str):
        self.rule = rule
        super().__init__(f"Guardrail [{rule}]: {message}")


def _invalid_constant(_value):
    raise ValueError("non-finite JSON number")


def decode_object(raw: bytes, limit: int) -> dict:
    if len(raw) > limit:
        raise Denied("payload_size", "Payload exceeds the demo size limit.")
    try:
        value = json.loads(raw, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Denied("invalid_payload", "Expected a valid JSON object.") from None
    if not isinstance(value, dict):
        raise Denied("invalid_payload", "Expected a valid JSON object.")
    return value


def check_request(targets: list[str], raw: bytes) -> None:
    params = decode_object(raw, MAX_REQUEST_BYTES)
    if len(targets) != 1 or targets[0] not in TOOLS or TOOLS[targets[0]] != params.get("name"):
        raise Denied("tool_scope", "Tool is outside the demo policy scope.")
    args = params.get("arguments", {})
    if not isinstance(args, dict):
        raise Denied("invalid_arguments", "Tool arguments must be an object.")
    target = targets[0]
    if target == "hello":
        message = args.get("message", "hello")
        if set(args) - {"message"} or not isinstance(message, str):
            raise Denied("invalid_arguments", "Hello accepts only a text message.")
        if len(message) > MAX_MESSAGE_CHARS:
            raise Denied("message_length", "Hello messages must be at most 280 characters.")
        if SECRET.search(unicodedata.normalize("NFKC", message)):
            raise Denied("secret_input", "Remove secret-like content before calling this tool.")
    elif target == "math":
        if set(args) != {"a", "b"}:
            raise Denied("invalid_arguments", "Math requires exactly a and b.")
        for value in args.values():
            # Reject bool and numeric strings rather than relying on tool coercion.
            if type(value) not in (int, float) or not -MAX_NUMBER <= value <= MAX_NUMBER:
                raise Denied("number_range", "Math inputs must be finite numbers between -1000 and 1000.")
            if isinstance(value, float) and not math.isfinite(value):
                raise Denied("number_range", "Math inputs must be finite numbers between -1000 and 1000.")
    elif args:
        raise Denied("invalid_arguments", "Time accepts no arguments.")


def redact_response(raw: bytes) -> bytes | None:
    """Scrub string values in text AND structured content, preserving JSON types."""
    result = decode_object(raw, MAX_RESPONSE_BYTES)

    def redact(value):
        if isinstance(value, str):
            return EMAIL.sub("[REDACTED_EMAIL]", value)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    try:
        clean = redact(result)
    except RecursionError:
        raise Denied("invalid_payload", "Response is too deeply nested.") from None
    if clean == result:
        return None
    return json.dumps(clean, ensure_ascii=True, allow_nan=False).encode()
