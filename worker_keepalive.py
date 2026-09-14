import json
import time

import websocket


# Wrapper de estabilidad para Railway <-> Deriv.
# No cambia la estrategia, riesgo, balance ni lógica de operaciones.
# Solo añade tolerancia a timeouts y keepalive al WebSocket.

_ORIGINAL_CREATE_CONNECTION = websocket.create_connection


class KeepAliveWebSocket:
    def __init__(self, ws, max_timeouts=3):
        self._ws = ws
        self._max_timeouts = max(1, int(max_timeouts))
        self._consecutive_timeouts = 0
        self._last_timeout_log = 0.0

    def __getattr__(self, name):
        return getattr(self._ws, name)

    def send(self, *args, **kwargs):
        return self._ws.send(*args, **kwargs)

    def close(self, *args, **kwargs):
        return self._ws.close(*args, **kwargs)

    def recv(self, *args, **kwargs):
        while True:
            try:
                raw = self._ws.recv(*args, **kwargs)
                if self._consecutive_timeouts:
                    print(
                        "[CloudSim][WS] conexion recuperada; llegaron datos otra vez.",
                        flush=True,
                    )
                self._consecutive_timeouts = 0
                return raw

            except websocket.WebSocketTimeoutException as exc:
                self._consecutive_timeouts += 1
                now = time.time()

                if now - self._last_timeout_log >= 5:
                    print(
                        f"[CloudSim][WS] timeout {self._consecutive_timeouts}/"
                        f"{self._max_timeouts}; enviando keepalive...",
                        flush=True,
                    )
                    self._last_timeout_log = now

                try:
                    self._ws.ping("cloudsim-keepalive")
                except Exception as ping_exc:
                    print(
                        f"[CloudSim][WS] fallo ping websocket: {ping_exc}",
                        flush=True,
                    )
                    raise

                try:
                    self._ws.send(json.dumps({"ping": 1}))
                except Exception as app_ping_exc:
                    print(
                        f"[CloudSim][WS] fallo ping Deriv: {app_ping_exc}",
                        flush=True,
                    )
                    raise

                if self._consecutive_timeouts >= self._max_timeouts:
                    raise TimeoutError(
                        "WebSocket sin datos despues de varios keepalive; "
                        "forzando reconexion limpia."
                    ) from exc


def create_connection_keepalive(*args, **kwargs):
    kwargs["timeout"] = 20
    ws = _ORIGINAL_CREATE_CONNECTION(*args, **kwargs)
    print("[CloudSim][WS] conexion abierta con keepalive activo.", flush=True)
    return KeepAliveWebSocket(ws, max_timeouts=3)


websocket.create_connection = create_connection_keepalive

import worker_simulation  # noqa: E402


if __name__ == "__main__":
    worker_simulation.main()
