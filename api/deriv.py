"""Cliente REST + WebSocket para la Options API actual de Deriv.

Soporta dos modos:
- secuencial, compatible con los scripts históricos del proyecto;
- con lector en segundo hilo, usado por el motor conectado para poder recibir
  ticks y actualizaciones de contratos mientras espera respuestas con req_id.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import unicodedata
from typing import Any

import requests
import websocket

from config import (
    DERIV_HTTP_TIMEOUT_SECONDS,
    DERIV_REQUEST_TIMEOUT_SECONDS,
    DERIV_WS_TIMEOUT_SECONDS,
)


class DerivAPIError(RuntimeError):
    def __init__(self, message, code=None, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class DerivConnectionError(RuntimeError):
    pass


def _normalize_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


class DerivClient:
    def __init__(
        self,
        app_id,
        access_token,
        api_base,
        *,
        threaded=False,
        allow_real=False,
        request_timeout=DERIV_REQUEST_TIMEOUT_SECONDS,
    ):
        self.app_id = app_id
        self.access_token = access_token
        self.api_base = api_base.rstrip("/")
        self.threaded = threaded
        self.allow_real = allow_real
        self.request_timeout = request_timeout
        self.ws = None
        self.account = None
        self.account_type = None

        self._headers = {
            "Deriv-App-ID": self.app_id,
            "Authorization": f"Bearer {self.access_token}",
        }
        self._req_id = 0
        self._req_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._events = queue.Queue()
        self._reader_thread = None
        self._running = threading.Event()
        self._reader_error = None

    # ------------------------------------------------------------------
    # REST / conexión
    # ------------------------------------------------------------------
    def _rest_json(self, method, url):
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers,
                timeout=DERIV_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DerivConnectionError(
                f"No se pudo conectar con Deriv ({type(exc).__name__}): {exc}"
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise DerivConnectionError("Deriv devolvió una respuesta REST no JSON") from exc

    def get_accounts(self):
        payload = self._rest_json("GET", f"{self.api_base}/accounts")
        if payload.get("errors"):
            first = payload["errors"][0]
            raise DerivAPIError(first.get("message", "Error al listar cuentas"), first.get("code"), first)
        data = payload.get("data", [])
        return data if isinstance(data, list) else [data]

    def get_otp(self, account_id):
        payload = self._rest_json(
            "POST", f"{self.api_base}/accounts/{account_id}/otp"
        )
        if payload.get("errors"):
            first = payload["errors"][0]
            raise DerivAPIError(first.get("message", "Error al solicitar OTP"), first.get("code"), first)
        try:
            return payload["data"]["url"]
        except (KeyError, TypeError) as exc:
            raise DerivConnectionError("La respuesta OTP no contiene una URL WebSocket") from exc

    @staticmethod
    def _account_kind(account):
        candidates = (
            account.get("account_type"),
            account.get("type"),
            account.get("environment"),
        )
        joined = " ".join(str(value or "") for value in candidates).lower()
        if any(word in joined for word in ("demo", "virtual")):
            return "demo"
        if any(word in joined for word in ("real", "financial")):
            return "real"
        account_id = str(account.get("account_id", "")).upper()
        if account_id.startswith(("VRTC", "DOT")) and "DEMO" in joined:
            return "demo"
        return str(account.get("account_type", "")).lower()

    def connect(self, account_type="demo", account_id=None):
        account_type = account_type.lower().strip()
        if account_type == "real" and not self.allow_real:
            raise RuntimeError("La conexión REAL no fue autorizada por el proceso llamador")
        if account_type not in {"demo", "real"}:
            raise ValueError("account_type debe ser 'demo' o 'real'")

        accounts = self.get_accounts()
        selected = None
        if account_id:
            selected = next(
                (item for item in accounts if str(item.get("account_id")) == str(account_id)),
                None,
            )
        if selected is None:
            selected = next(
                (item for item in accounts if self._account_kind(item) == account_type),
                None,
            )
        if selected is None:
            available = [
                {"account_id": item.get("account_id"), "type": self._account_kind(item)}
                for item in accounts
            ]
            raise RuntimeError(
                f"No se encontró una cuenta {account_type}. Disponibles: {available}"
            )

        selected_kind = self._account_kind(selected)
        if selected_kind and selected_kind != account_type:
            raise RuntimeError(
                f"La cuenta {selected.get('account_id')} es {selected_kind}, no {account_type}"
            )

        ws_url = self.get_otp(selected["account_id"])
        self.ws = websocket.create_connection(
            ws_url,
            timeout=DERIV_WS_TIMEOUT_SECONDS,
            enable_multithread=True,
        )
        self.account = selected
        self.account_type = account_type
        self._reader_error = None
        if self.threaded:
            self._start_reader()
        return selected

    # ------------------------------------------------------------------
    # Transporte WebSocket
    # ------------------------------------------------------------------
    def _next_req_id(self):
        with self._req_lock:
            self._req_id += 1
            return self._req_id

    def _send_json(self, payload):
        if not self.ws:
            raise DerivConnectionError("WebSocket no conectado")
        with self._send_lock:
            self.ws.send(json.dumps(payload))

    @staticmethod
    def _raise_api_error(response):
        error = response.get("error")
        if error:
            raise DerivAPIError(
                error.get("message", "Error de Deriv"),
                code=error.get("code"),
                details=error,
            )
        errors = response.get("errors")
        if errors:
            first = errors[0]
            raise DerivAPIError(
                first.get("message", "Error de Deriv"),
                code=first.get("code"),
                details=first,
            )

    def request(self, payload, timeout=None):
        timeout = timeout or self.request_timeout
        request_payload = dict(payload)
        request_payload.setdefault("req_id", self._next_req_id())
        req_id = request_payload["req_id"]

        if not self.threaded:
            self._send_json(request_payload)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                response = json.loads(self.ws.recv())
                if response.get("req_id") == req_id:
                    self._raise_api_error(response)
                    return response
            raise TimeoutError(f"Deriv no respondió req_id={req_id}")

        response_queue = queue.Queue()
        with self._pending_lock:
            self._pending[req_id] = response_queue
        removed = False
        try:
            self._send_json(request_payload)
            try:
                response = response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                if self._reader_error:
                    raise DerivConnectionError(str(self._reader_error)) from self._reader_error
                raise TimeoutError(f"Deriv no respondió req_id={req_id}") from exc
            # Retira el req_id antes de devolver la respuesta. Así, las
            # actualizaciones posteriores de una suscripción pasan al queue
            # de eventos y no quedan atrapadas como si fueran otra respuesta.
            with self._pending_lock:
                self._pending.pop(req_id, None)
                removed = True
            while True:
                try:
                    extra = response_queue.get_nowait()
                except queue.Empty:
                    break
                self._events.put(extra)
            self._raise_api_error(response)
            return response
        finally:
            if not removed:
                with self._pending_lock:
                    self._pending.pop(req_id, None)

    def _start_reader(self):
        self._running.set()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="deriv-ws-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop(self):
        try:
            while self._running.is_set() and self.ws:
                raw = self.ws.recv()
                if not raw:
                    raise DerivConnectionError("Deriv cerró el WebSocket")
                response = json.loads(raw)
                req_id = response.get("req_id")
                pending_queue = None
                if req_id is not None:
                    with self._pending_lock:
                        pending_queue = self._pending.get(req_id)
                if pending_queue is not None:
                    pending_queue.put(response)
                else:
                    self._events.put(response)
        except Exception as exc:  # el motor decide si reconecta
            if self._running.is_set():
                self._reader_error = exc
                self._events.put(
                    {
                        "msg_type": "connection_error",
                        "error": {"message": str(exc), "code": type(exc).__name__},
                    }
                )
        finally:
            self._running.clear()

    def recv_event(self, timeout=1.0):
        if not self.threaded:
            raise RuntimeError("recv_event requiere DerivClient(threaded=True)")
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def is_connected(self):
        return bool(self.ws) and (not self.threaded or self._running.is_set())

    # ------------------------------------------------------------------
    # Datos y cuenta
    # ------------------------------------------------------------------
    def get_candles(self, symbol, granularity=60, count=1000, end="latest"):
        response = self.request(
            {
                "ticks_history": symbol,
                "end": end,
                "count": min(int(count), 1000),
                "style": "candles",
                "granularity": int(granularity),
                "adjust_start_time": 1,
            }
        )
        return response.get("candles", [])

    def get_historical_candles(self, symbol, granularity=60, count=4000):
        remaining = max(0, int(count))
        end = "latest"
        collected = []
        while remaining > 0:
            block_size = min(1000, remaining)
            block = self.get_candles(symbol, granularity, block_size, end=end)
            if not block:
                break
            collected = block + collected
            remaining -= len(block)
            oldest = int(block[0]["epoch"])
            end = oldest - int(granularity)
            if len(block) < block_size:
                break
        # Evita duplicados si el servidor solapa bordes de paginación.
        unique = {int(item["epoch"]): item for item in collected}
        return [unique[key] for key in sorted(unique)][-count:]

    def get_active_symbols(self, full=True):
        response = self.request(
            {"active_symbols": "full" if full else "brief", }
        )
        return response.get("active_symbols", [])

    @staticmethod
    def symbol_code(item):
        return item.get("underlying_symbol") or item.get("symbol")

    @staticmethod
    def symbol_name(item):
        return item.get("underlying_symbol_name") or item.get("display_name") or DerivClient.symbol_code(item)

    @classmethod
    def resolve_symbol_from_list(cls, query, symbols):
        normalized_query = _normalize_text(query)
        if not normalized_query:
            raise ValueError("El símbolo no puede estar vacío")

        for item in symbols:
            code = cls.symbol_code(item)
            if code and str(code).lower() == str(query).lower():
                return item
        for item in symbols:
            if _normalize_text(cls.symbol_name(item)) == normalized_query:
                return item

        # Alias tolerantes: "Crash 500", "Crash 500 Index", etc.
        query_tokens = set(normalized_query.split())
        scored = []
        for item in symbols:
            code = cls.symbol_code(item)
            name = cls.symbol_name(item)
            normalized_name = _normalize_text(name)
            normalized_code = _normalize_text(code)
            tokens = set(normalized_name.split()) | set(normalized_code.split())
            common = len(query_tokens & tokens)
            if normalized_query in normalized_name or normalized_name in normalized_query:
                common += 10
            if common:
                scored.append((common, len(normalized_name), item))
        if not scored:
            raise RuntimeError(f"Deriv no devolvió un símbolo compatible con {query!r}")
        scored.sort(key=lambda row: (-row[0], row[1]))
        best = scored[0]
        if best[0] < max(1, len(query_tokens) - 1):
            raise RuntimeError(f"No se pudo resolver con seguridad el símbolo {query!r}")
        return best[2]

    def resolve_symbol(self, query):
        return self.resolve_symbol_from_list(query, self.get_active_symbols(full=True))

    def get_balance(self, subscribe=False):
        response = self.request({"balance": 1, **({"subscribe": 1} if subscribe else {})})
        return response

    def get_portfolio(self):
        return self.request({"portfolio": 1})

    def get_contracts_for(self, symbol):
        return self.request({"contracts_for": symbol})

    # ------------------------------------------------------------------
    # Suscripciones y trading
    # ------------------------------------------------------------------
    def subscribe_ticks(self, symbol):
        return self.request({"ticks": symbol, "subscribe": 1})

    def request_proposal(
        self,
        *,
        symbol,
        direction,
        amount,
        currency,
        multiplier,
        stop_loss,
        take_profit,
    ):
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        return self.request(
            {
                "proposal": 1,
                "amount": round(float(amount), 2),
                "basis": "stake",
                "contract_type": contract_type,
                "currency": currency,
                "duration_unit": "s",
                "multiplier": int(multiplier),
                "underlying_symbol": symbol,
                "limit_order": {
                    "stop_loss": round(float(stop_loss), 2),
                    "take_profit": round(float(take_profit), 2),
                },
            }
        )

    def buy_contract(self, proposal_id, max_price):
        return self.request(
            {"buy": str(proposal_id), "price": round(float(max_price), 2)}
        )

    def subscribe_open_contract(self, contract_id):
        return self.request(
            {
                "proposal_open_contract": 1,
                "contract_id": int(contract_id),
                "subscribe": 1,
            }
        )

    def sell_contract(self, contract_id, price=0):
        return self.request({"sell": int(contract_id), "price": float(price)})

    def ping(self):
        return self.request({"ping": 1}, timeout=min(10, self.request_timeout))

    def disconnect(self):
        self._running.clear()
        ws = self.ws
        self.ws = None
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        with self._pending_lock:
            for pending_queue in self._pending.values():
                pending_queue.put(
                    {
                        "error": {
                            "message": "Conexión cerrada",
                            "code": "ConnectionClosed",
                        }
                    }
                )
            self._pending.clear()
