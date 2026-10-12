"""Configuración persistente en data/config.json."""

import copy
import json
import logging
import threading

from . import paths

log = logging.getLogger(__name__)

DEFAULT_SERVER = "https://ntfy.sh"
MAX_RECENT_SERVERS = 15
MAX_RECENT_TOPICS = 25

DEFAULTS = {
	"recent_servers": [DEFAULT_SERVER],
	"recent_topics": {},
	"min_toast_priority": 1,
	"show_toasts": True,
	"toast_actions": True,
	"close_to_tray": True,
	"start_minimized": False,
	"register_aumid": True,
	"mark_read_on_select": True,
	"retention_days": 0,
	"language": "",
	"last_publish": {},
	"window": {},
	# Proxy: "system", "none", "http", "https", "socks4" o "socks5". La contraseña va en el almacén cifrado.
	"proxy": {"type": "system", "host": "", "port": 0, "username": "", "has_password": False},
}


def normalize_server(url: str) -> str:
	"""Normaliza la URL de un servidor: añade https:// si falta y quita la barra final."""
	url = (url or "").strip()
	if not url:
		return ""
	if "://" not in url:
		url = "https://" + url
	return url.rstrip("/")


class Config:
	def __init__(self, path=None):
		self.path = path or paths.config_file()
		self._lock = threading.RLock()
		self.data = copy.deepcopy(DEFAULTS)
		self.load()

	def load(self) -> None:
		if not self.path.exists():
			return
		try:
			loaded = json.loads(self.path.read_text(encoding="utf-8"))
		except (OSError, ValueError):
			log.exception("No se pudo leer la configuración; se usan valores por defecto")
			return
		if isinstance(loaded, dict):
			self.data.update(loaded)

	def save(self) -> None:
		with self._lock:
			raw = json.dumps(self.data, ensure_ascii=False, indent="\t").encode("utf-8")
			paths.atomic_write_bytes(self.path, raw)

	def get(self, key, default=None):
		with self._lock:
			if key in self.data:
				return self.data[key]
			return DEFAULTS.get(key, default)

	def set(self, key, value, save=True) -> None:
		with self._lock:
			self.data[key] = value
			if save:
				self.save()

	# Servidores y temas recientes

	def recent_servers(self) -> list[str]:
		servers = list(self.get("recent_servers") or [])
		return servers or [DEFAULT_SERVER]

	def add_recent_server(self, server: str, save=True) -> None:
		server = normalize_server(server)
		if not server:
			return
		with self._lock:
			servers = [s for s in self.recent_servers() if s != server]
			servers.insert(0, server)
			self.data["recent_servers"] = servers[:MAX_RECENT_SERVERS]
			if save:
				self.save()

	def remove_recent_server(self, server: str) -> None:
		server = normalize_server(server)
		with self._lock:
			self.data["recent_servers"] = [s for s in self.recent_servers() if s != server]
			self.data.get("recent_topics", {}).pop(server, None)
			self.save()

	def recent_topics(self, server: str) -> list[str]:
		return list((self.get("recent_topics") or {}).get(normalize_server(server), []))

	def add_recent_topic(self, server: str, topic: str, save=True) -> None:
		server = normalize_server(server)
		topic = (topic or "").strip()
		if not server or not topic:
			return
		with self._lock:
			all_topics = self.data.setdefault("recent_topics", {})
			topics = [t for t in all_topics.get(server, []) if t != topic]
			topics.insert(0, topic)
			all_topics[server] = topics[:MAX_RECENT_TOPICS]
			if save:
				self.save()
