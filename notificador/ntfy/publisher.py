"""Publicación de mensajes en servidores ntfy (https://docs.ntfy.sh/publish/)."""

import base64
import json
import os
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from .. import APP_NAME, APP_VERSION
from ..config import normalize_server
from ..i18n import _
from .models import (
	MAX_ACTIONS,
	MAX_MESSAGE_BYTES,
	MAX_TAGS_BYTES,
	MAX_TITLE_BYTES,
	Message,
	PublishRequest,
	is_valid_topic,
)

USER_AGENT = f"{APP_NAME}/{APP_VERSION}"
TIMEOUT = (10, 60)
UPLOAD_TIMEOUT = (10, 600)

# Límites documentados en https://docs.ntfy.sh/publish/#limitations
DEFAULT_FILE_SIZE = 15 * 1024 * 1024
DEFAULT_TOTAL_SIZE = 100 * 1024 * 1024
NTFY_SH_FILE_SIZE = 2 * 1024 * 1024
NTFY_SH_TOTAL_SIZE = 20 * 1024 * 1024
DEFAULT_EXPIRY = 3 * 3600

TEMPLATES = ("yes", "github", "grafana", "alertmanager")


class PublishError(Exception):
	def __init__(self, message: str, status: int = 0, code: int = 0):
		super().__init__(message)
		self.status = status
		self.code = code


class PublishCancelled(Exception):
	pass


# Autenticación


def auth_header(cred: dict | None) -> str | None:
	"""Valor de la cabecera Authorization para una credencial del almacén."""
	if not cred:
		return None
	if cred.get("type") == "token":
		token = cred.get("secret") or ""
		return f"Bearer {token}" if token else None
	if cred.get("type") == "basic":
		user = cred.get("username") or ""
		password = cred.get("secret") or ""
		raw = f"{user}:{password}".encode("utf-8")
		return "Basic " + base64.b64encode(raw).decode("ascii")
	return None


def auth_query_value(header: str) -> str:
	"""Valor del parámetro ?auth=: base64 sin relleno de la cabecera Authorization completa."""
	return base64.urlsafe_b64encode(header.encode("utf-8")).decode("ascii").rstrip("=")


def base_headers(auth: str | None = None) -> dict:
	headers = {"User-Agent": USER_AGENT}
	if auth:
		headers["Authorization"] = auth
	return headers


def describe_http_error(response: requests.Response) -> "PublishError":
	"""Convierte una respuesta de error de ntfy ({code, http, error}) en una excepción legible."""
	detail = ""
	code = 0
	try:
		doc = response.json()
		detail = str(doc.get("error") or "")
		code = int(doc.get("code") or 0)
	except (ValueError, AttributeError):
		detail = response.text.strip()[:300]
	status = response.status_code
	known = {
		400: _("Petición incorrecta"),
		401: _("Se requiere autenticación o las credenciales no son válidas"),
		403: _("No tienes permiso para acceder a este tema"),
		404: _("No encontrado"),
		413: _("El mensaje o el adjunto es demasiado grande"),
		429: _("Se han superado los límites del servidor; inténtalo más tarde"),
		507: _("El servidor no tiene espacio para más adjuntos"),
	}
	text = known.get(status, _("Error del servidor"))
	message = f"{text} (HTTP {status})"
	if detail:
		message += f": {detail}"
	return PublishError(message, status, code)


# Límites de adjuntos


@dataclass
class AttachmentLimits:
	file_size: int
	total_remaining: int
	expiry: int
	from_server: bool

	@property
	def enabled(self) -> bool:
		return self.file_size > 0


def default_limits(server: str) -> AttachmentLimits:
	host = (urlsplit(normalize_server(server)).hostname or "").lower()
	if host == "ntfy.sh":
		return AttachmentLimits(NTFY_SH_FILE_SIZE, NTFY_SH_TOTAL_SIZE, DEFAULT_EXPIRY, False)
	return AttachmentLimits(DEFAULT_FILE_SIZE, DEFAULT_TOTAL_SIZE, DEFAULT_EXPIRY, False)


def parse_account_limits(doc: dict, server: str) -> AttachmentLimits:
	fallback = default_limits(server)
	limits = doc.get("limits") or {}
	stats = doc.get("stats") or {}
	if "attachment_file_size" not in limits:
		return fallback
	file_size = int(limits.get("attachment_file_size") or 0)
	total = int(limits.get("attachment_total_size") or 0)
	remaining = stats.get("attachment_total_size_remaining")
	remaining = int(remaining) if remaining is not None else total
	expiry = int(limits.get("attachment_expiry_duration") or fallback.expiry)
	return AttachmentLimits(file_size, remaining, expiry, True)


def fetch_limits(server: str, auth: str | None = None, session=None) -> AttachmentLimits:
	"""Consulta /v1/account para conocer los límites reales; si falla usa los documentados."""
	server = normalize_server(server)
	http = session or requests
	try:
		response = http.get(f"{server}/v1/account", headers=base_headers(auth), timeout=TIMEOUT)
		if response.ok:
			return parse_account_limits(response.json(), server)
	except (requests.RequestException, ValueError):
		pass
	return default_limits(server)


def format_size(size: int) -> str:
	value = float(size)
	for unit in ("B", "KB", "MB", "GB"):
		if value < 1024 or unit == "GB":
			if unit == "B":
				return f"{int(value)} {unit}"
			text = f"{value:.1f}"
			if text.endswith(".0"):
				text = text[:-2]
			return f"{text} {unit}"
		value /= 1024
	return f"{size} B"


# Validación


def validate_file(path: str, limits: AttachmentLimits) -> str | None:
	"""Comprueba un archivo local frente a los límites. Devuelve un mensaje de error o None."""
	if not path:
		return _("No se ha indicado ningún archivo.")
	if not os.path.isfile(path):
		return _("El archivo no existe.")
	try:
		size = os.path.getsize(path)
		with open(path, "rb"):
			pass
	except OSError as exc:
		return _("No se puede leer el archivo: {error}").format(error=exc)
	if size == 0:
		return _("El archivo está vacío.")
	if not limits.enabled:
		return _("El servidor no admite adjuntos.")
	if size > limits.file_size:
		return _("El archivo ocupa {size} y el límite del servidor es {limit} por archivo.").format(
			size=format_size(size), limit=format_size(limits.file_size)
		)
	if limits.total_remaining >= 0 and size > limits.total_remaining:
		return _("El archivo ocupa {size} y solo quedan {remaining} de cuota de adjuntos.").format(
			size=format_size(size), remaining=format_size(limits.total_remaining)
		)
	return None


def validate_request(req: PublishRequest) -> list[tuple[str, str]]:
	"""Errores de validación independientes del servidor: lista de (campo, mensaje)."""
	errors = []
	server = normalize_server(req.server)
	if not server:
		errors.append(("server", _("Indica un servidor.")))
	else:
		parts = urlsplit(server)
		if parts.scheme not in ("http", "https") or not parts.hostname:
			errors.append(("server", _("La dirección del servidor no es válida.")))
	if not is_valid_topic(req.topic):
		errors.append(
			("topic", _("El tema debe tener entre 1 y 64 caracteres: letras, números, guiones o guiones bajos."))
		)
	if len(req.title.encode("utf-8")) > MAX_TITLE_BYTES:
		errors.append(("title", _("El título no puede superar 1 KB.")))
	if len(",".join(req.tags).encode("utf-8")) > MAX_TAGS_BYTES:
		errors.append(("tags", _("Las etiquetas no pueden superar 512 bytes en total.")))
	if not 1 <= int(req.priority) <= 5:
		errors.append(("priority", _("La prioridad debe estar entre 1 y 5.")))
	if len(req.actions) > MAX_ACTIONS:
		errors.append(("actions", _("Se admiten como máximo 3 acciones.")))
	for action in req.actions:
		if action.validate():
			errors.append(("actions", _("La acción «{label}» está incompleta.").format(label=action.label)))
	if req.file_path and req.attach_url:
		errors.append(("attach", _("Elige un archivo local o una URL externa, no ambos.")))
	if req.template and req.template not in TEMPLATES:
		errors.append(("template", _("Plantilla desconocida.")))
	if req.template and req.file_path:
		errors.append(("template", _("No se puede usar una plantilla al adjuntar un archivo local.")))
	return errors


def message_too_long(req: PublishRequest) -> bool:
	"""True si el mensaje excede 4096 bytes (el servidor lo convertiría en adjunto)."""
	return len(req.message.encode("utf-8")) > MAX_MESSAGE_BYTES


# Construcción de la petición


@dataclass
class HttpRequest:
	method: str
	url: str
	headers: dict
	params: dict
	server: str
	json_body: dict | None = None
	text_body: str | None = None
	file_path: str = ""


def _extra_headers(req: PublishRequest) -> dict:
	headers = {}
	if not req.cache:
		headers["X-Cache"] = "no"
	if not req.firebase:
		headers["X-Firebase"] = "no"
	if req.unifiedpush:
		headers["X-UnifiedPush"] = "1"
	if req.template:
		headers["X-Template"] = req.template
	return headers


_JSON_FIELDS = ("click", "icon", "filename", "delay", "timezone", "email", "call", "sequence_id")
_QUERY_FIELDS = ("click", "icon", "filename", "delay", "timezone", "email", "call")


def build_request(req: PublishRequest, auth: str | None = None) -> HttpRequest:
	"""Construye la petición HTTP.

	- Sin archivo local ni plantilla: POST JSON a la raíz del servidor (admite UTF-8 en todos los campos).
	- Con archivo local: PUT al tema con el archivo como cuerpo y el resto como parámetros de consulta.
	- Con plantilla: POST al tema con el cuerpo tal cual (JSON a transformar) y parámetros de consulta.
	"""
	server = normalize_server(req.server)
	headers = base_headers(auth)
	headers.update(_extra_headers(req))
	tags = [t.strip() for t in req.tags if t.strip()]
	actions = [a.to_dict() for a in req.actions]

	if not req.file_path and not req.template:
		body = {"topic": req.topic}
		if req.message:
			body["message"] = req.message
		if req.title:
			body["title"] = req.title
		if int(req.priority) != 3:
			body["priority"] = int(req.priority)
		if tags:
			body["tags"] = tags
		if req.markdown:
			body["markdown"] = True
		for key in _JSON_FIELDS:
			value = getattr(req, key).strip()
			if value:
				body[key] = value
		if req.attach_url.strip():
			body["attach"] = req.attach_url.strip()
		if actions:
			body["actions"] = actions
		headers["Content-Type"] = "application/json"
		return HttpRequest("POST", server + "/", headers, {}, server, json_body=body)

	params = {}
	if req.title:
		params["title"] = req.title
	if int(req.priority) != 3:
		params["priority"] = str(int(req.priority))
	if tags:
		params["tags"] = ",".join(tags)
	if req.markdown:
		params["markdown"] = "yes"
	for key in _QUERY_FIELDS:
		value = getattr(req, key).strip()
		if value:
			params[key] = value
	if req.sequence_id.strip():
		params["sid"] = req.sequence_id.strip()
	if req.attach_url.strip():
		params["attach"] = req.attach_url.strip()
	if actions:
		params["actions"] = json.dumps(actions, ensure_ascii=False)
	url = f"{server}/{req.topic}"
	if req.file_path:
		if req.message:
			params["message"] = req.message
		if not req.filename.strip():
			params["filename"] = os.path.basename(req.file_path)
		return HttpRequest("PUT", url, headers, params, server, file_path=req.file_path)
	return HttpRequest("POST", url, headers, params, server, text_body=req.message)


class _ProgressFile:
	"""Envoltorio de archivo que informa del progreso y permite cancelar la subida."""

	CHUNK = 64 * 1024

	def __init__(self, path, progress=None, cancel: threading.Event | None = None):
		self._fh = open(path, "rb")
		self._size = os.path.getsize(path)
		self._sent = 0
		self._progress = progress
		self._cancel = cancel

	def __len__(self):
		return self._size

	def read(self, amount=-1):
		if self._cancel is not None and self._cancel.is_set():
			raise PublishCancelled()
		if amount is None or amount < 0:
			amount = self.CHUNK
		chunk = self._fh.read(min(amount, self.CHUNK))
		self._sent += len(chunk)
		if self._progress:
			self._progress(self._sent, self._size)
		return chunk

	def close(self):
		self._fh.close()


def send(http_req: HttpRequest, progress=None, cancel=None, session=None) -> Message:
	"""Ejecuta la petición y devuelve el mensaje publicado según la respuesta del servidor."""
	http = session or requests
	fileobj = None
	kwargs = {"headers": http_req.headers, "timeout": TIMEOUT}
	try:
		if http_req.file_path:
			fileobj = _ProgressFile(http_req.file_path, progress, cancel)
			kwargs.update(params=http_req.params, data=fileobj, timeout=UPLOAD_TIMEOUT)
		elif http_req.json_body is not None:
			kwargs["data"] = json.dumps(http_req.json_body, ensure_ascii=False).encode("utf-8")
		else:
			kwargs.update(params=http_req.params, data=(http_req.text_body or "").encode("utf-8"))
		response = http.request(http_req.method, http_req.url, **kwargs)
	except PublishCancelled:
		raise
	except requests.RequestException as exc:
		if cancel is not None and cancel.is_set():
			raise PublishCancelled() from exc
		raise PublishError(_("No se pudo conectar con el servidor: {error}").format(error=exc)) from exc
	finally:
		if fileobj is not None:
			fileobj.close()
	if not response.ok:
		raise describe_http_error(response)
	try:
		return Message.from_dict(response.json(), http_req.server)
	except ValueError:
		return Message(id="", time=0, event="message", topic="", server=http_req.server)


def publish(req: PublishRequest, auth: str | None = None, progress=None, cancel=None, session=None) -> Message:
	return send(build_request(req, auth), progress, cancel, session)


def _sequence_request(method, server, topic, sequence_id, suffix, auth=None, session=None) -> None:
	http = session or requests
	url = f"{normalize_server(server)}/{topic}/{sequence_id}{suffix}"
	try:
		response = http.request(method, url, headers=base_headers(auth), timeout=TIMEOUT)
	except requests.RequestException as exc:
		raise PublishError(_("No se pudo conectar con el servidor: {error}").format(error=exc)) from exc
	if not response.ok:
		raise describe_http_error(response)


def clear_notification(server, topic, sequence_id, auth=None, session=None) -> None:
	"""Marca como leída y descarta una notificación en los clientes (PUT /<tema>/<id>/clear)."""
	_sequence_request("PUT", server, topic, sequence_id, "/clear", auth, session)


def delete_notification(server, topic, sequence_id, auth=None, session=None) -> None:
	"""Elimina una notificación de los clientes (DELETE /<tema>/<id>)."""
	_sequence_request("DELETE", server, topic, sequence_id, "", auth, session)
