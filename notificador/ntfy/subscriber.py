"""Suscripción a servidores ntfy (https://docs.ntfy.sh/subscribe/api/).

Se usa preferentemente WebSocket (/<temas>/ws). Si el servidor o un proxy no lo permiten,
se recurre al flujo HTTP de JSON (/<temas>/json). Todas las conexiones viven en un único hilo
con un bucle asyncio; los temas de un mismo servidor comparten conexión.
"""

import asyncio
import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import requests
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidProxy, InvalidStatus, InvalidURI

from .. import net
from ..config import normalize_server
from .models import Message
from .publisher import USER_AGENT, auth_query_value

log = logging.getLogger(__name__)

RECV_TIMEOUT = 120  # ntfy envía keepalive cada 45 s por defecto
BACKOFF_MIN = 1
BACKOFF_MAX = 60
AUTH_RETRY = 300

# Estados de conexión
CONNECTING = "connecting"
CONNECTED = "connected"
WAITING = "waiting"
ERROR = "error"
AUTH_ERROR = "auth_error"
STOPPED = "stopped"


@dataclass
class EndpointConfig:
	"""Conexión a un servidor con uno o varios temas.

	topics: {tema: marca de tiempo mínima}. Los mensajes más antiguos que esa marca se descartan,
	de modo que una suscripción nueva no recibe el histórico pedido por otra del mismo servidor.
	Una marca 0 significa «todo el histórico disponible».
	"""

	server: str
	topics: dict = field(default_factory=dict)
	auth: str | None = None

	def signature(self):
		return (self.server, tuple(sorted(self.topics)), self.auth)


@dataclass
class ConnectionStatus:
	server: str
	state: str
	detail: str = ""
	transport: str = ""
	topics: tuple = ()
	since: float = 0


class _Connection:
	def __init__(self, manager: "SubscriptionManager", config: EndpointConfig, server_time: int = 0):
		self.manager = manager
		self.config = config
		self.server_time = server_time
		self.task: asyncio.Task | None = None
		self.stopping = False
		self.use_query_auth = False
		self.prefer_http = False
		self._http_response = None
		self.connect_time = 0

	# Utilidades

	def _status(self, state, detail="", transport=""):
		self.manager._set_status(
			ConnectionStatus(
				self.config.server, state, detail, transport, tuple(sorted(self.config.topics)), time.time()
			)
		)

	def _since_param(self) -> str | None:
		floors = list(self.config.topics.values())
		if not floors:
			return None
		base = min(floors)
		if self.server_time:
			# Reconexión: pedir desde la última hora de servidor conocida (con margen).
			base = max(base, self.server_time - 5)
		if base <= 0:
			return "all"
		return str(int(base))

	def _url(self, kind: str) -> str:
		server = normalize_server(self.config.server)
		parts = urlsplit(server)
		topics = ",".join(sorted(self.config.topics))
		params = {}
		since = self._since_param()
		if since:
			params["since"] = since
		if self.config.auth and self.use_query_auth:
			params["auth"] = auth_query_value(self.config.auth)
		query = ("?" + urlencode(params)) if params else ""
		path = parts.path.rstrip("/")
		if kind == "ws":
			scheme = "wss" if parts.scheme == "https" else "ws"
			return f"{scheme}://{parts.netloc}{path}/{topics}/ws{query}"
		return f"{server}/{topics}/json{query}"

	def _headers(self) -> dict:
		headers = {}
		if self.config.auth and not self.use_query_auth:
			headers["Authorization"] = self.config.auth
		return headers

	def _handle_line(self, text: str, transport: str) -> None:
		text = text.strip()
		if not text:
			return
		try:
			data = json.loads(text)
		except ValueError:
			log.warning("Mensaje no válido de %s: %r", self.config.server, text[:200])
			return
		event = data.get("event")
		event_time = int(data.get("time") or 0)
		if event_time:
			self.server_time = max(self.server_time, event_time)
		if event == "open":
			self._status(CONNECTED, transport=transport)
			return
		if event in ("keepalive", "poll_request"):
			return
		if event not in ("message", "message_delete", "message_clear"):
			log.debug("Evento desconocido %s", event)
			return
		msg = Message.from_dict(data, self.config.server)
		floor = self.config.topics.get(msg.topic)
		if floor is not None and floor > 0 and msg.time and msg.time < floor:
			return
		backlog = bool(msg.time) and msg.time < self.connect_time - 2
		self.manager._dispatch(msg, backlog)

	# Bucle principal

	async def run(self) -> None:
		backoff = BACKOFF_MIN
		while not self.stopping:
			self.connect_time = time.time()
			self._status(CONNECTING)
			try:
				if self.prefer_http:
					await asyncio.to_thread(self._http_stream)
				else:
					await self._websocket()
				backoff = BACKOFF_MIN
				if self.stopping:
					break
				detail = "closed"
			except asyncio.CancelledError:
				raise
			except InvalidStatus as exc:
				status = exc.response.status_code
				if status in (401, 403):
					if self.config.auth and not self.use_query_auth:
						# Algunos proxies eliminan la cabecera; probar con ?auth=
						self.use_query_auth = True
						continue
					self._status(AUTH_ERROR, f"HTTP {status}")
					await self._sleep(AUTH_RETRY)
					continue
				if status in (400, 404, 405, 426, 501):
					log.info("WebSocket no disponible en %s (HTTP %s); se usa HTTP", self.config.server, status)
					self.prefer_http = True
					continue
				detail = f"HTTP {status}"
			except _HttpAuthError as exc:
				if self.config.auth and not self.use_query_auth:
					self.use_query_auth = True
					continue
				self._status(AUTH_ERROR, str(exc))
				await self._sleep(AUTH_RETRY)
				continue
			except (InvalidURI, InvalidProxy, ValueError, ImportError) as exc:
				# Configuración errónea (p. ej. proxy no válido o SOCKS sin python-socks): reintentar despacio.
				self._status(ERROR, str(exc))
				await self._sleep(AUTH_RETRY)
				continue
			except (OSError, InvalidHandshake, ConnectionClosed, asyncio.TimeoutError, requests.RequestException) as exc:
				detail = str(exc) or exc.__class__.__name__
			except Exception as exc:  # noqa: BLE001 - nunca dejar morir la tarea
				log.exception("Error inesperado en la conexión con %s", self.config.server)
				detail = str(exc)
			if self.stopping:
				break
			delay = min(BACKOFF_MAX, backoff) * (0.8 + random.random() * 0.4)
			self._status(WAITING, detail)
			await self._sleep(delay)
			backoff = min(BACKOFF_MAX, backoff * 2)
		self._status(STOPPED)

	async def _sleep(self, seconds: float) -> None:
		end = time.monotonic() + seconds
		while not self.stopping and time.monotonic() < end:
			await asyncio.sleep(min(1.0, end - time.monotonic()))

	async def _websocket(self) -> None:
		url = self._url("ws")
		async with connect(
			url,
			additional_headers=self._headers(),
			proxy=net.websocket_proxy(),
			user_agent_header=USER_AGENT,
			open_timeout=20,
			max_size=4 * 1024 * 1024,
		) as ws:
			while not self.stopping:
				raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
				if isinstance(raw, bytes):
					raw = raw.decode("utf-8", "replace")
				self._handle_line(raw, "websocket")

	def _http_stream(self) -> None:
		"""Flujo HTTP de JSON (bloqueante, se ejecuta en un hilo auxiliar)."""
		url = self._url("json")
		headers = {"User-Agent": USER_AGENT, **self._headers()}
		with net.session() as http, http.get(url, headers=headers, stream=True, timeout=(15, RECV_TIMEOUT)) as response:
			if response.status_code in (401, 403):
				raise _HttpAuthError(f"HTTP {response.status_code}")
			response.raise_for_status()
			self._http_response = response
			for line in response.iter_lines(decode_unicode=False):
				if self.stopping:
					break
				if line:
					self._handle_line(line.decode("utf-8", "replace"), "http")
		self._http_response = None

	def cancel(self) -> None:
		self.stopping = True
		response = self._http_response
		if response is not None:
			try:
				response.close()
			except Exception:  # noqa: BLE001
				pass
		if self.task is not None:
			self.task.cancel()


class _HttpAuthError(Exception):
	pass


def _loop_exception_handler(loop, context) -> None:
	"""Rebaja a depuración el ruido de websockets cuando un proxy cierra la conexión en el handshake."""
	if isinstance(context.get("exception"), EOFError):
		log.debug("Conexión cerrada durante el handshake: %s", context.get("message"))
		return
	loop.default_exception_handler(context)


class SubscriptionManager:
	"""Mantiene las conexiones abiertas. Los callbacks se invocan desde el hilo de red.

	on_message(msg: Message, backlog: bool)
	on_status(status: ConnectionStatus)
	"""

	def __init__(self, on_message, on_status):
		self.on_message = on_message
		self.on_status = on_status
		self._loop: asyncio.AbstractEventLoop | None = None
		self._thread: threading.Thread | None = None
		self._connections: dict[str, _Connection] = {}
		self._statuses: dict[str, ConnectionStatus] = {}
		self._lock = threading.Lock()
		self._ready = threading.Event()

	# Ciclo de vida

	def start(self) -> None:
		if self._thread is not None:
			return
		self._thread = threading.Thread(target=self._run_loop, name="ntfy-subscriber", daemon=True)
		self._thread.start()
		self._ready.wait(5)

	def _run_loop(self) -> None:
		self._loop = asyncio.new_event_loop()
		asyncio.set_event_loop(self._loop)
		self._loop.set_exception_handler(_loop_exception_handler)
		self._ready.set()
		try:
			self._loop.run_forever()
		finally:
			self._loop.close()

	def stop(self, timeout: float = 5) -> None:
		if self._loop is None:
			return
		future = asyncio.run_coroutine_threadsafe(self._stop_all(), self._loop)
		try:
			future.result(timeout)
		except Exception:  # noqa: BLE001
			pass
		self._loop.call_soon_threadsafe(self._loop.stop)
		if self._thread is not None:
			self._thread.join(timeout)
		self._thread = None
		self._loop = None

	async def _stop_all(self) -> None:
		connections = list(self._connections.values())
		self._connections.clear()
		for conn in connections:
			conn.cancel()
		await asyncio.gather(*(c.task for c in connections if c.task), return_exceptions=True)

	# Configuración

	def update(self, configs: list[EndpointConfig]) -> None:
		"""Aplica la lista de conexiones deseadas (seguro desde cualquier hilo)."""
		if self._loop is None:
			self.start()
		asyncio.run_coroutine_threadsafe(self._apply(list(configs)), self._loop)

	def reconnect_all(self) -> None:
		if self._loop is None:
			return
		asyncio.run_coroutine_threadsafe(self._reconnect(), self._loop)

	async def _apply(self, configs: list[EndpointConfig]) -> None:
		wanted = {normalize_server(c.server): c for c in configs if c.topics}
		for server, conn in list(self._connections.items()):
			new = wanted.get(server)
			if new is None or new.signature() != conn.config.signature():
				conn.cancel()
				self._connections.pop(server, None)
				if new is None:
					with self._lock:
						self._statuses.pop(server, None)
					self.on_status(ConnectionStatus(server, STOPPED))
			else:
				conn.config.topics = dict(new.topics)
		for server, config in wanted.items():
			if server in self._connections:
				continue
			config.server = server
			conn = _Connection(self, config)
			self._connections[server] = conn
			conn.task = asyncio.ensure_future(conn.run())

	async def _reconnect(self) -> None:
		configs = [c.config for c in self._connections.values()]
		times = {s: c.server_time for s, c in self._connections.items()}
		await self._stop_all()
		for config in configs:
			conn = _Connection(self, config, times.get(config.server, 0))
			self._connections[config.server] = conn
			conn.task = asyncio.ensure_future(conn.run())

	# Notificación de eventos

	def _set_status(self, status: ConnectionStatus) -> None:
		with self._lock:
			self._statuses[status.server] = status
		try:
			self.on_status(status)
		except Exception:  # noqa: BLE001
			log.exception("Error en on_status")

	def _dispatch(self, msg: Message, backlog: bool) -> None:
		try:
			self.on_message(msg, backlog)
		except Exception:  # noqa: BLE001
			log.exception("Error procesando mensaje")

	def statuses(self) -> list[ConnectionStatus]:
		with self._lock:
			return list(self._statuses.values())
