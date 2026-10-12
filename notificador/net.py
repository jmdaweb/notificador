"""Configuración de red compartida: servidor proxy para requests y websockets.

Modos:
- "system": configuración del sistema (variables de entorno y opciones de Internet de Windows).
  Es lo que hacen requests y websockets por defecto, a través de urllib.request.getproxies().
- "none": conexión directa, ignorando cualquier proxy del sistema.
- "http", "https", "socks4", "socks5": proxy indicado por el usuario.

Según la documentación de requests, los proxies de una Session pueden quedar sustituidos
por los del entorno, así que se pasan en cada petición (ver ProxySession.request).
Los SOCKS resuelven los nombres en el proxy (socks4a / socks5h).
"""

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import quote

import requests

PROXY_SYSTEM = "system"
PROXY_NONE = "none"
PROXY_TYPES = (PROXY_SYSTEM, PROXY_NONE, "http", "https", "socks4", "socks5")
MANUAL_TYPES = ("http", "https", "socks4", "socks5")

# websockets usa el puerto 80 si un proxy SOCKS no lo indica, así que siempre se envía explícito.
DEFAULT_PORTS = {"http": 8080, "https": 443, "socks4": 1080, "socks5": 1080}
SCHEMES = {"http": "http", "https": "https", "socks4": "socks4a", "socks5": "socks5h"}

# Clave del almacén cifrado donde se guarda la contraseña del proxy.
VAULT_SECRET = "proxy"


@dataclass(frozen=True)
class ProxySettings:
	type: str = PROXY_SYSTEM
	host: str = ""
	port: int = 0
	username: str = ""
	password: str = ""

	@classmethod
	def from_config(cls, data: dict | None, password: str = "") -> "ProxySettings":
		data = data or {}
		kind = data.get("type") if data.get("type") in PROXY_TYPES else PROXY_SYSTEM
		try:
			port = int(data.get("port") or 0)
		except (TypeError, ValueError):
			port = 0
		return cls(
			type=kind,
			host=str(data.get("host") or "").strip(),
			port=port,
			username=str(data.get("username") or ""),
			password=password or "",
		)

	def to_config(self) -> dict:
		"""Datos para config.json (la contraseña va al almacén cifrado, nunca aquí)."""
		return {
			"type": self.type,
			"host": self.host,
			"port": self.port,
			"username": self.username,
			"has_password": bool(self.password),
		}

	@property
	def manual(self) -> bool:
		return self.type in MANUAL_TYPES

	@property
	def effective_port(self) -> int:
		return self.port or DEFAULT_PORTS.get(self.type, 0)

	def validate(self) -> str | None:
		"""Código de error («missing_host», «invalid_port») o None."""
		if not self.manual:
			return None
		if not self.host or any(c in self.host for c in "/@?# "):
			return "missing_host"
		if not 0 <= self.port <= 65535:
			return "invalid_port"
		return None

	def url(self) -> str | None:
		"""URL del proxy con credenciales (p. ej. «socks5h://usuario:clave@host:1080»)."""
		if not self.manual or self.validate():
			return None
		host = self.host
		if ":" in host and not host.startswith("["):
			host = f"[{host}]"  # IPv6
		auth = ""
		if self.username:
			# websockets rechaza un usuario sin contraseña; se envía la contraseña vacía.
			auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
		return f"{SCHEMES[self.type]}://{auth}{host}:{self.effective_port}"

	def display(self) -> str:
		"""Descripción sin contraseña, para registros y mensajes."""
		if not self.manual:
			return self.type
		user = f"{self.username}@" if self.username else ""
		return f"{SCHEMES[self.type]}://{user}{self.host}:{self.effective_port}"


_lock = threading.Lock()
_current = ProxySettings()


def configure(settings: ProxySettings) -> None:
	global _current
	with _lock:
		_current = settings


def current() -> ProxySettings:
	with _lock:
		return _current


def requests_proxies(settings: ProxySettings | None = None) -> dict | None:
	"""Argumento «proxies» para requests, o None para dejar la configuración del sistema."""
	settings = settings or current()
	if settings.type == PROXY_SYSTEM:
		return None
	if settings.type == PROXY_NONE:
		# Un valor None por clave elimina el proxy que aportaría el entorno.
		return {"http": None, "https": None, "all": None}
	url = settings.url()
	return {"http": url, "https": url}


def websocket_proxy(settings: ProxySettings | None = None):
	"""Argumento «proxy» de websockets.asyncio.client.connect."""
	settings = settings or current()
	if settings.type == PROXY_SYSTEM:
		return True
	if settings.type == PROXY_NONE:
		return None
	return settings.url()


class ProxySession(requests.Session):
	"""Session que aplica el proxy configurado a cada petición."""

	def __init__(self, settings: ProxySettings | None = None):
		super().__init__()
		self._settings = settings

	def request(self, method, url, **kwargs):
		proxies = requests_proxies(self._settings)
		if proxies is not None and "proxies" not in kwargs:
			kwargs["proxies"] = proxies
		return super().request(method, url, **kwargs)


def session(settings: ProxySettings | None = None) -> ProxySession:
	return ProxySession(settings)


@contextmanager
def use(http=None):
	"""Usa la sesión indicada (p. ej. en pruebas) o crea una temporal con el proxy actual."""
	if http is not None:
		yield http
		return
	with session() as s:
		yield s


def describe_error(exc: Exception) -> str:
	"""Mensaje legible para un fallo de conexión de requests, distinguiendo los del proxy."""
	from .i18n import _

	text = str(exc)
	if len(text) > 300:
		text = text[:299] + "…"
	if "Missing dependencies for SOCKS" in text:
		return _("Falta el soporte para proxies SOCKS (instala «requests[socks]»).")
	if "407" in text:
		return _("El servidor proxy ha rechazado las credenciales (HTTP 407).")
	if isinstance(exc, requests.exceptions.ProxyError) or "socks" in text.lower():
		return _("No se pudo conectar a través del servidor proxy: {error}").format(error=text)
	return _("No se pudo conectar con el servidor: {error}").format(error=text)


def check(settings: ProxySettings, url: str, user_agent: str, timeout=(10, 15)) -> int:
	"""Hace una petición de prueba a través del proxy indicado. Devuelve el código HTTP."""
	with session(settings) as s:
		response = s.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
		return response.status_code
