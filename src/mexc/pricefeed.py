"""Feed de precios en tiempo real por WebSocket (opcional).

Mantiene en un hilo propio una conexión al WebSocket de MEXC suscrita a las
operaciones (deals) de los símbolos configurados, y expone el último precio
de cada uno. El motor lo usa para comprobar stop-loss y trailing cada pocos
segundos, en vez de esperar al siguiente poll REST.

Diseño defensivo (el bot NUNCA depende de que el feed funcione):
  - `price()` devuelve None si no hay dato o si el último es más viejo que
    MAX_AGE: el motor entonces sigue con el precio REST de cada ciclo.
  - Reconexión automática con backoff exponencial si la conexión se cae.
  - El parseo tolera varios formatos de mensaje (deals y tickers JSON).
    Nota: MEXC está migrando su WebSocket spot a protobuf; si los canales
    JSON dejan de servir datos, este feed simplemente quedará sin precios
    frescos y el bot continuará operando por REST. La URL es configurable
    (`websocket_url`) por si cambia el endpoint.
  - `websocket-client` es una dependencia opcional: sin ella, el feed se
    deshabilita con un aviso.
"""
import json
import threading
import time

from ..logger import get_logger

log = get_logger("pricefeed")


class WebSocketPriceFeed:
    DEFAULT_URL = "wss://wbs.mexc.com/ws"
    MAX_AGE = 10.0        # segundos: un precio más viejo que esto no se usa
    PING_EVERY = 25.0     # ping de aplicación para mantener viva la conexión
    RECONNECT_MAX = 60.0  # tope del backoff de reconexión

    def __init__(self, symbols: list[str], url: str | None = None):
        self.symbols = [s.upper() for s in symbols]
        self.url = url or self.DEFAULT_URL
        self._prices: dict[str, tuple[float, float]] = {}  # symbol -> (precio, ts)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Arranca el hilo del feed. False si falta websocket-client."""
        try:
            import websocket  # noqa: F401 (import perezoso: dependencia opcional)
        except ImportError:
            log.warning("websocket-client no está instalado: el feed WebSocket "
                        "queda deshabilitado (pip install websocket-client).")
            return False
        self._thread = threading.Thread(target=self._run, name="ws-feed", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def price(self, symbol: str) -> float | None:
        """Último precio del símbolo, o None si no hay dato FRESCO."""
        with self._lock:
            entry = self._prices.get(symbol.upper())
        if entry is None:
            return None
        value, ts = entry
        if time.monotonic() - ts > self.MAX_AGE:
            return None  # dato viejo: mejor que el motor use REST
        return value

    # ------------------------------------------------------------------
    def _run(self) -> None:
        import websocket

        backoff = 1.0
        while not self._stop.is_set():
            try:
                conn = websocket.create_connection(self.url, timeout=10)
                self._subscribe(conn)
                log.info("Feed WS conectado a %s | símbolos: %s",
                         self.url, ", ".join(self.symbols))
                backoff = 1.0
                conn.settimeout(5)
                last_ping = time.monotonic()
                while not self._stop.is_set():
                    if time.monotonic() - last_ping > self.PING_EVERY:
                        conn.send(json.dumps({"method": "PING"}))
                        last_ping = time.monotonic()
                    try:
                        message = conn.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if message:
                        self._handle_message(message)
                conn.close()
            except Exception as exc:  # noqa: BLE001 - reconectar siempre
                if self._stop.is_set():
                    break
                log.warning("Feed WS caído (%s). Reintento en %.0fs...", exc, backoff)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self.RECONNECT_MAX)

    def _subscribe(self, conn) -> None:
        channels = [f"spot@public.deals.v3.api@{s}" for s in self.symbols]
        conn.send(json.dumps({"method": "SUBSCRIPTION", "params": channels}))

    def _handle_message(self, message) -> None:
        """Extrae (símbolo, precio) de un mensaje. Tolerante a formatos:
        deals (d.deals[].p) y tickers (d.p); ignora PONGs y acks."""
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        symbol = str(data.get("s") or "").upper()
        body = data.get("d")
        if not symbol or not isinstance(body, dict):
            return  # PONG, ack de suscripción, etc.
        raw_price = None
        deals = body.get("deals")
        if isinstance(deals, list) and deals:
            raw_price = deals[-1].get("p")  # última operación del lote
        elif "p" in body:
            raw_price = body.get("p")
        try:
            value = float(raw_price)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        with self._lock:
            self._prices[symbol] = (value, time.monotonic())
