"""MitraFrameParser — tolerant WebSocket frame normaliser.

WHAT THIS MODULE IS FOR
=======================
The Mitra WebSocket emits several distinct payload shapes depending on which
code path inside Django handled the turn. This module normalises all of them
into one ``Frame`` dataclass so the rest of Saarthi never has to branch on
wire format.

THREE VERIFIED DEFECTS IN MITRA'S PROTOCOL (§1.2, §1.3)
=========================================================

**Defect 1 — User-echo (§1.2).** After Saarthi sends a user message, Mitra
immediately echoes it back on the same socket with ``text.source == "user"``
*before* dispatching the bot reply to Celery
(``async_consumer.py:88-96``). Without filtering, every user turn appears
twice in the Saarthi transcript — once as the actual user message and once as
a spurious "bot" reply. ``Frame.kind == FrameKind.USER_ECHO`` marks these so
the channel can discard them without touching any higher-level logic.

**Defect 2 — Chunked delivery (§1.3).** Free-flow replies arrive as many
frames with ``finish_reason == null`` and ``text.type == "chunk"``. The
terminating frame carries a truthy ``finish_reason``. Reading only the first
frame truncates every free-flow answer. The *accumulation* of chunks into a
complete turn is the responsibility of ``MitraChannel``; the parser's only
job is to set ``Frame.finish_reason`` correctly so the channel knows when to
stop accumulating.

**Defect 3 — Three ``extra_content`` shapes (§1.3).** Mitra emits options /
choices in three structurally different objects:

  * ``{question, options}`` — state-machine flow with choice buttons
    (``celery_tasks/handle_message.py:32-62``)
  * ``{problem_statement, should_move_forward, validation}`` — story
    evaluation frame (``mitra_bedrock_tasks.py:79-83``)
  * ``{sources: [...]}`` — free-flow RAG citations

All three are normalised to ``Frame.options: list[ParsedOption]``. The
channel sees one shape regardless of which Mitra code path fired.

DESIGN RULES
============
* ``parse(raw)`` NEVER raises. Malformed JSON, missing keys, wrong types —
  all yield ``Frame(kind=FrameKind.ERROR, error=<reason>)``.
* The parser is a pure function (no I/O, no state). Chunk accumulation is
  done by the caller.
* Legacy envelope types from the Node bot reference client are handled
  defensively so the parser does not break if Mitra's wire format varies
  between deployments.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class FrameKind(Enum):
    """Discriminator for the normalised frame.

    The channel uses this to decide what to do with each frame:

      BOT_CHUNK  — accumulate msg into the current turn
      BOT_FINAL  — last chunk of the current turn (finish_reason is truthy)
      USER_ECHO  — Mitra echoing the user's own message back; DISCARD
      SYSTEM     — system/control frame (session_end, error from Mitra)
      ERROR      — parse failure; log and discard
    """
    BOT_CHUNK = auto()    # source==bot, finish_reason falsy
    BOT_FINAL = auto()    # source==bot, finish_reason truthy
    USER_ECHO = auto()    # source==user  →  §1.2 — DISCARD
    SYSTEM    = auto()    # source==system or legacy control envelope
    ERROR     = auto()    # malformed frame


@dataclass(frozen=True)
class ParsedOption:
    """One choice button offered alongside a bot turn.

    Normalised from all three ``extra_content`` shapes (§1.3):
      - state-machine: options[i].{id?, label?, value?, text?}
      - story validation: synthesised from problem_statement
      - RAG sources: synthesised from sources[i].{url?, title?, text?}
    """
    id: str
    label: str
    value: str


@dataclass(frozen=True)
class Frame:
    """Normalised Mitra WebSocket frame.

    ``kind``          — what the channel should do with this frame
    ``source``        — raw value of text.source (for logging / debugging)
    ``msg``           — accumulated text fragment (empty string if none)
    ``step``          — interview step number reported by Mitra
    ``finish_reason`` — truthy when this is the last chunk of a bot turn
    ``options``       — normalised choice buttons (may be empty)
    ``error``         — human-readable parse/system error (non-empty on ERROR / SYSTEM)
    """
    kind: FrameKind
    source: str = ""
    msg: str = ""
    step: int = 0
    finish_reason: Optional[str] = None
    options: list[ParsedOption] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _coerce_str(v: object, default: str = "") -> str:
    """Return v as str; return default on None or non-str types."""
    if v is None:
        return default
    if isinstance(v, str):
        return v
    return str(v)


def _coerce_int(v: object, default: int = 0) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalise_options(extra_content: object) -> list[ParsedOption]:
    """Map all three ``extra_content`` shapes to a flat ``ParsedOption`` list.

    Shape 1 — state-machine: ``{question: str, options: list[dict]}``
      Each dict may carry keys: id, label, value, text (use whichever exist).

    Shape 2 — story validation: ``{problem_statement, should_move_forward, validation}``
      No user-facing choices; yields an empty list.
      (should_move_forward is a machine signal, not a UI affordance.)

    Shape 3 — RAG sources: ``{sources: list[dict]}``
      Each source has url/title/text; surface as tappable citations.
    """
    if not isinstance(extra_content, dict):
        return []

    # Shape 1 — state machine choices
    if "options" in extra_content:
        raw_opts = extra_content["options"]
        if isinstance(raw_opts, list):
            result = []
            for i, item in enumerate(raw_opts):
                if not isinstance(item, dict):
                    continue
                # Prefer explicit id → fall back to ordinal string
                opt_id    = _coerce_str(item.get("id") or item.get("value"), str(i))
                opt_label = _coerce_str(
                    item.get("label") or item.get("text") or item.get("value"), opt_id
                )
                opt_value = _coerce_str(item.get("value") or item.get("label"), opt_id)
                result.append(ParsedOption(id=opt_id, label=opt_label, value=opt_value))
            return result

    # Shape 2 — story validation (machine signal, no UI choices)
    if "problem_statement" in extra_content:
        return []

    # Shape 3 — free-flow RAG sources → surface as citations
    if "sources" in extra_content:
        raw_sources = extra_content["sources"]
        if isinstance(raw_sources, list):
            result = []
            for i, item in enumerate(raw_sources):
                if not isinstance(item, dict):
                    continue
                src_id    = _coerce_str(item.get("url") or item.get("id"), str(i))
                src_label = _coerce_str(
                    item.get("title") or item.get("text") or item.get("url"), src_id
                )
                src_value = _coerce_str(item.get("url") or item.get("text"), src_id)
                result.append(ParsedOption(id=src_id, label=src_label, value=src_value))
            return result

    return []


def _parse_text_envelope(text: dict) -> Frame:
    """Parse the primary ``{"text": {...}}`` envelope (§1.2 / §1.3)."""
    source        = _coerce_str(text.get("source"))
    msg           = _coerce_str(text.get("msg"))
    step          = _coerce_int(text.get("step"))
    finish_reason = text.get("finish_reason")   # keep None vs "" distinction
    extra_content = text.get("extra_content")
    error_val     = _coerce_str(text.get("error"))

    # §1.2 — user-echo: source == "user"
    if source == "user":
        return Frame(
            kind=FrameKind.USER_ECHO,
            source=source,
            msg=msg,
            step=step,
        )

    # System / error frame
    if source == "system" or error_val:
        return Frame(
            kind=FrameKind.SYSTEM,
            source=source,
            msg=msg,
            step=step,
            error=error_val or msg,
        )

    # Bot turn — chunked or final
    options = _normalise_options(extra_content)
    if finish_reason:
        kind = FrameKind.BOT_FINAL
    else:
        kind = FrameKind.BOT_CHUNK

    return Frame(
        kind=kind,
        source=source,
        msg=msg,
        step=step,
        finish_reason=_coerce_str(finish_reason) if finish_reason else None,
        options=options,
    )


# Legacy envelope types seen in the Node bot reference client.
# Values are the FrameKind they map to when source is absent.
_LEGACY_SYSTEM_TYPES = frozenset({
    "session_end", "error", "connected", "disconnected",
})
_LEGACY_BOT_TYPES = frozenset({
    "message", "bot_response", "response",
})
_LEGACY_OPTION_TYPES = frozenset({
    "options", "choices",
})


def _parse_legacy_envelope(raw: dict) -> Frame:
    """Handle legacy envelope formats that wrap the payload differently.

    The Node bot reference client logs several type values that differ from
    the primary ``{"text": {...}}`` shape. Handle them defensively so the
    parser works across Mitra deployment variants.
    """
    envelope_type = _coerce_str(raw.get("type")).lower()
    msg           = _coerce_str(
        raw.get("msg") or raw.get("message") or raw.get("text") or raw.get("content")
    )
    error_val     = _coerce_str(raw.get("error") or raw.get("err"))

    if envelope_type in _LEGACY_SYSTEM_TYPES:
        return Frame(
            kind=FrameKind.SYSTEM,
            source="system",
            msg=msg,
            error=error_val or (msg if envelope_type == "error" else ""),
        )

    if envelope_type in _LEGACY_OPTION_TYPES:
        # Normalise the choice list the same way as extra_content.options
        raw_opts = raw.get("options") or raw.get("choices") or []
        extra = {"options": raw_opts} if raw_opts else {}
        return Frame(
            kind=FrameKind.BOT_FINAL,
            source="bot",
            msg=msg,
            finish_reason="stop",
            options=_normalise_options(extra),
        )

    if envelope_type in _LEGACY_BOT_TYPES or envelope_type == "text":
        finish_reason = _coerce_str(raw.get("finish_reason")) or None
        return Frame(
            kind=FrameKind.BOT_FINAL if finish_reason else FrameKind.BOT_CHUNK,
            source="bot",
            msg=msg,
            finish_reason=finish_reason,
        )

    # Unknown type — treat as a system/info frame
    return Frame(
        kind=FrameKind.SYSTEM,
        source=_coerce_str(raw.get("source")),
        msg=msg,
        error=f"unrecognised envelope type: {envelope_type!r}",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(raw: str | bytes) -> Frame:
    """Parse one raw WebSocket message into a normalised ``Frame``.

    This function NEVER raises. All errors are captured as
    ``Frame(kind=FrameKind.ERROR, error=<reason>)``.

    Args:
        raw: The raw WebSocket message (str or bytes).

    Returns:
        A ``Frame`` describing the normalised content.
    """
    # Step 1 — JSON decode
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        payload = json.loads(raw)
    except Exception as exc:
        return Frame(kind=FrameKind.ERROR, error=f"JSON decode error: {exc}")

    if not isinstance(payload, dict):
        return Frame(kind=FrameKind.ERROR, error=f"expected JSON object, got {type(payload).__name__}")

    # Step 2 — route on envelope shape
    text = payload.get("text")
    if isinstance(text, dict):
        # Primary shape: {"text": {"msg", "source", ...}}
        try:
            return _parse_text_envelope(text)
        except Exception as exc:
            return Frame(kind=FrameKind.ERROR, error=f"text envelope parse error: {exc}")

    if isinstance(text, str):
        # Degenerate shape: {"text": "<raw string>"} — treat as a bot chunk
        finish_reason = _coerce_str(payload.get("finish_reason")) or None
        return Frame(
            kind=FrameKind.BOT_FINAL if finish_reason else FrameKind.BOT_CHUNK,
            source="bot",
            msg=text,
            finish_reason=finish_reason,
        )

    # Step 3 — legacy envelope (type field present, no text dict)
    if "type" in payload or any(k in payload for k in ("message", "msg", "content")):
        try:
            return _parse_legacy_envelope(payload)
        except Exception as exc:
            return Frame(kind=FrameKind.ERROR, error=f"legacy envelope parse error: {exc}")

    return Frame(kind=FrameKind.ERROR, error="unrecognised frame shape — no 'text' key")
