"""MitraChannel -- one socket, one daemon reader thread, one bounded frame queue.

Implements the connection lifecycle described in the design doc (§7.3,
§1.1): connect with the Origin/User-Agent Mitra gates on, send the
`authenticate` frame, then settle for `handshake.settle_ms` WITHOUT waiting
for an acknowledgement -- verified (§1.1) that Mitra never sends one on any
path. `send_and_await_turn` accumulates chunked bot replies (§1.3), discards
the user-echo frame Mitra sends back before dispatching the real reply
(§1.2), and falls back to whatever was accumulated so far when fragments
stop arriving (the idle-gap backstop), rather than hanging until the full
turn timeout.

Deliberately NOT part of this module: MitraSessionManager (the
conversation_id -> MitraChannel LRU map, design doc §7.4). That's a
separate class with its own re-authentication/eviction concerns.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import websocket

from src.integrations.mitra.exceptions import (
    MitraChannelClosed,
    MitraRemoteError,
    MitraTurnTimeout,
    MitraConcurrentTurnError,
)
from src.integrations.mitra.frame_parser import Frame, ParsedOption, parse
from src.logger import get_logger

logger = get_logger("mitra_ws_channel")


@dataclass(frozen=True)
class BotTurn:
    """The result of one send_and_await_turn() call."""
    text: str
    options: List[ParsedOption] = field(default_factory=list)
    step: Optional[int] = None


class MitraChannel:
    def __init__(
        self,
        spec,           # src.domain.agent_spec.RemoteSpec
        sess,           # src.agents.protocol.AgentSessionView
        settings,       # src.settings.Settings
        ws_factory: Callable[[], "websocket.WebSocket"] = websocket.WebSocket,
    ):
        self._q: "queue.Queue[Frame]" = queue.Queue(maxsize=512)
        self._pending: Optional[Frame] = None  # settling pushback slot, §7.3
        self._closed = threading.Event()
        self._close_reason = ""
        self._turn_lock = threading.Lock()

        self._ws = ws_factory()
        self._ws.connect(
            settings.mitra_ws_url,
            origin=settings.mitra_origin_url,  # Mitra gates on Origin -- §13.2
            header=[
                f"User-Agent: {settings.mitra_user_agent}",
                "Accept-Language: en-GB,en-US;q=0.9,en;q=0.8",
            ],
            timeout=settings.mitra_ws_connect_timeout_s,
            enable_multithread=True,
        )

        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name=f"mitra-ws-{sess.id}"
        )
        self._reader.start()

        self._authenticate(spec, sess, settings)

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        try:
            while True:
                raw = self._ws.recv()
                if raw == "":
                    break
                try:
                    self._q.put(parse(raw), timeout=1)
                except queue.Full:
                    # Sustained consumer stall past the bounded queue size --
                    # treat as fatal rather than blocking the reader forever.
                    self._close_reason = "frame queue full; consumer stalled"
                    break
        except Exception as e:
            self._close_reason = str(e)
        finally:
            self._closed.set()

    def _try_get(self, timeout: float) -> Optional[Frame]:
        if self._pending is not None:
            f, self._pending = self._pending, None
            return f
        if timeout <= 0:
            return None
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _pushback(self, frame: Frame) -> None:
        self._pending = frame

    def _drain_stale(self) -> None:
        """Drop leftover frames (stale echoes) sitting in the queue before a
        new turn starts. Deliberately does NOT touch self._pending -- a bot
        frame pushed back during settling still belongs to the upcoming
        (possibly first) turn."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    # ------------------------------------------------------------------
    # Establishment
    # ------------------------------------------------------------------

    def _authenticate(self, spec, sess, settings) -> None:
        self._ws.send(json.dumps({
            "type": "authenticate",
            "sessionid": sess.remote_session_id,
            "profileid": sess.remote_profile_id,
            "projectid": "",
            "taskid": None,
            "access_token": None,
            "route": sess.language,
            "bot_route": sess.remote_bot_route,
            "flow_name": spec.flow_name,
            "address": {
                "ipCity": settings.mitra_ip_city,
                "ipState": settings.mitra_ip_state,
                "ipZipCode": settings.mitra_ip_zip,
            },
        }))

        # §1.1 -- Mitra never acks. Wait for one if it ever appears
        # (defensive, for a future Mitra release), otherwise just settle.
        deadline = time.monotonic() + spec.handshake.settle_ms / 1000
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if self._closed.is_set():
                raise MitraChannelClosed(self._close_reason)
            f = self._try_get(timeout=remaining)
            if f is None:
                continue
            if f.source in spec.handshake.ack_types:
                return
            if f.source == "bot":
                self._pushback(f)  # the bot opened first -- keep it for the turn
                return

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def send_and_await_turn(self, text: str, timeout_s: float, idle_gap_s: float) -> BotTurn:
        if not self._turn_lock.acquire(blocking=False):
            raise MitraConcurrentTurnError()
        try:
            self._drain_stale()  # leftover echoes, §1.2

            self._ws.send(json.dumps({"type": "message", "text": text, "context": "", "asr_audio": None}))

            chunks: List[str] = []
            options: List[ParsedOption] = []
            step: Optional[int] = None
            deadline = time.monotonic() + timeout_s
            last_rx = time.monotonic()

            while True:
                if self._closed.is_set():
                    raise MitraChannelClosed(self._close_reason)

                remaining = min(deadline - time.monotonic(), last_rx + idle_gap_s - time.monotonic())
                if remaining <= 0:
                    if chunks:
                        break  # idle-gap (or timeout) flush
                    raise MitraTurnTimeout(step=step)

                f = self._try_get(timeout=remaining)
                if f is None:
                    continue
                if f.source == "user":
                    continue  # §1.2 ECHO -- DISCARD
                if f.source == "system" and f.error:
                    raise MitraRemoteError(f.msg or f.error)
                if f.source != "bot":
                    continue

                last_rx = time.monotonic()
                if f.msg:
                    chunks.append(f.msg)  # §1.3 ACCUMULATE
                if f.step is not None:
                    step = f.step
                if f.options:
                    options = f.options
                if f.finish_reason:
                    break  # §1.3 END OF TURN

            return BotTurn(text="".join(chunks), options=options, step=step)
        finally:
            self._turn_lock.release()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return not self._closed.is_set()

    def close(self, quiet: bool = False) -> None:
        self._closed.set()
        try:
            self._ws.close()
        except Exception:
            pass
        if not quiet:
            logger.warning(f"MitraChannel closed (reason={self._close_reason!r})")
