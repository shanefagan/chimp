"""Minimal obs-websocket v5 client (built into OBS 28+), on Qt's event loop.

Connects only when a pad uses an OBS action, reconnects every few seconds while
OBS is closed, and tracks recording/streaming/scene/mute state for the pad LEDs.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

OBS_WS_CONFIG = Path.home() / ".config/obs-studio/plugin_config/obs-websocket/config.json"

# EventSubscription bits: Scenes 4, Inputs 8, Outputs 64
EVENTS = 4 | 8 | 64


def _obs_password() -> str:
    try:
        return json.loads(OBS_WS_CONFIG.read_text()).get("server_password", "")
    except (OSError, ValueError):
        return ""


def _auth(password: str, salt: str, challenge: str) -> str:
    """The obs-websocket v5 login answer, exactly as the protocol defines it.

    This isn't password storage: OBS sends a fresh salt and challenge on every connect
    and computes the same two SHA-256 steps on its side, so the algorithm is OBS's
    choice and anything slower (bcrypt, argon2) would simply fail to log in. Nothing
    hashed is kept. See docs/generated/protocol.md#creating-an-authentication-string
    in obs-websocket. (Code scanners flag this as weak password hashing: a false positive.)
    """
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient(QObject):
    stateChanged = Signal()
    connectedChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host, self.port, self.password = "localhost", 4455, ""
        self.watch_inputs: set[str] = set()
        self.enabled = False

        self.recording = False
        self.streaming = False
        self.scene = ""
        self.muted: dict[str, bool] = {}

        self._identified = False
        self._next_id = 0
        self._mute_requests: dict[str, str] = {}  # request id -> input name
        self._ws = QWebSocket()
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.disconnected.connect(self._on_disconnected)
        self._retry = QTimer(self, interval=5000, timeout=self._try_connect)

    # --- control --------------------------------------------------------------

    def configure(self, enabled: bool, host: str, port: int, password: str, inputs: set[str]) -> None:
        changed = (host, port, password) != (self.host, self.port, self.password)
        self.host, self.port, self.password = host, port, password
        self.watch_inputs = inputs
        self.enabled = enabled
        if not enabled:
            self._retry.stop()
            self._ws.close()
            return
        if changed:
            self._ws.close()
        if not self._retry.isActive():
            self._retry.start()
        if self._identified:
            self._refresh_inputs()
        else:
            self._try_connect()

    @property
    def connected(self) -> bool:
        return self._identified

    def request(self, request_type: str, data: dict | None = None) -> str:
        """Send a request; returns its id, or "" when not connected."""
        if not self._identified:
            return ""
        self._next_id += 1
        rid = f"{request_type}:{self._next_id}"
        msg = {"op": 6, "d": {"requestType": request_type, "requestId": rid}}
        if data:
            msg["d"]["requestData"] = data
        self._ws.sendTextMessage(json.dumps(msg))
        return rid

    # --- connection -------------------------------------------------------------

    def _try_connect(self) -> None:
        if not self.enabled or self._identified:
            return
        from PySide6.QtNetwork import QAbstractSocket

        if self._ws.state() != QAbstractSocket.SocketState.UnconnectedState:
            return
        self._ws.open(QUrl(f"ws://{self.host}:{self.port}"))

    def _on_disconnected(self) -> None:
        was = self._identified
        self._identified = False
        if was:
            self.connectedChanged.emit(False)

    def _on_message(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError:
            return
        op, d = msg.get("op"), msg.get("d") or {}
        if op == 0:  # Hello
            ident = {"rpcVersion": 1, "eventSubscriptions": EVENTS}
            auth = d.get("authentication")
            if auth:
                ident["authentication"] = _auth(self.password or _obs_password(), auth["salt"], auth["challenge"])
            self._ws.sendTextMessage(json.dumps({"op": 1, "d": ident}))
        elif op == 2:  # Identified
            self._identified = True
            self.connectedChanged.emit(True)
            self.request("GetRecordStatus")
            self.request("GetStreamStatus")
            self.request("GetCurrentProgramScene")
            self._refresh_inputs()
        elif op == 5:  # Event
            self._on_event(d.get("eventType", ""), d.get("eventData") or {})
        elif op == 7:  # RequestResponse
            if (d.get("requestStatus") or {}).get("result"):
                self._on_response(d.get("requestType", ""), d.get("responseData") or {}, d.get("requestId", ""))

    def _refresh_inputs(self) -> None:
        for name in self.watch_inputs:
            rid = self.request("GetInputMute", {"inputName": name})
            if rid:
                self._mute_requests[rid] = name

    def _on_response(self, kind: str, data: dict, rid: str) -> None:
        if kind == "GetRecordStatus":
            self.recording = bool(data.get("outputActive"))
        elif kind == "GetStreamStatus":
            self.streaming = bool(data.get("outputActive"))
        elif kind == "GetCurrentProgramScene":
            self.scene = data.get("sceneName") or data.get("currentProgramSceneName") or ""
        elif kind == "GetInputMute":
            name = self._mute_requests.pop(rid, None)
            if name is None:
                return
            self.muted[name] = bool(data.get("inputMuted"))
        else:
            return
        self.stateChanged.emit()

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "RecordStateChanged":
            self.recording = bool(data.get("outputActive"))
        elif kind == "StreamStateChanged":
            self.streaming = bool(data.get("outputActive"))
        elif kind == "CurrentProgramSceneChanged":
            self.scene = data.get("sceneName", "")
        elif kind == "InputMuteStateChanged":
            self.muted[data.get("inputName", "")] = bool(data.get("inputMuted"))
        else:
            return
        self.stateChanged.emit()
