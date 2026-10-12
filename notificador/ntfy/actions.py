"""Ejecución de las acciones asociadas a una notificación (https://docs.ntfy.sh/publish/#action-buttons)."""

import logging
import os
import webbrowser

import requests

from ..i18n import _
from .models import Action
from .publisher import USER_AGENT

log = logging.getLogger(__name__)

# Acciones que se pueden ejecutar en Windows. «broadcast» es exclusiva de Android.
SUPPORTED = ("view", "http", "copy")


class ActionError(Exception):
	pass


def open_url(url: str) -> None:
	"""Abre una URL con el programa asociado (navegador, cliente de correo, etc.)."""
	if not url:
		raise ActionError(_("La acción no tiene URL."))
	try:
		if url.lower().startswith(("http://", "https://")):
			webbrowser.open(url)
		else:
			os.startfile(url)  # mailto:, geo:, tel:, ...
	except OSError as exc:
		raise ActionError(_("No se pudo abrir «{url}»: {error}").format(url=url, error=exc)) from exc


def run_http(action: Action, session=None) -> str:
	"""Ejecuta una acción HTTP (bloqueante). Devuelve un texto con el resultado."""
	http = session or requests
	method = (action.method or "POST").upper()
	headers = {"User-Agent": USER_AGENT, **(action.headers or {})}
	try:
		response = http.request(
			method,
			action.url,
			headers=headers,
			data=action.body.encode("utf-8") if action.body else None,
			timeout=(10, 30),
		)
	except requests.RequestException as exc:
		raise ActionError(_("La petición ha fallado: {error}").format(error=exc)) from exc
	if not response.ok:
		raise ActionError(
			_("El servidor respondió con un error: HTTP {status} {reason}").format(
				status=response.status_code, reason=response.reason
			)
		)
	return _("Acción completada: HTTP {status} {reason}").format(status=response.status_code, reason=response.reason)


def describe(action: Action) -> str:
	"""Descripción accesible del destino de la acción."""
	if action.action == "view":
		return _("Abre {url}").format(url=action.url)
	if action.action == "http":
		return _("Envía una petición {method} a {url}").format(method=(action.method or "POST").upper(), url=action.url)
	if action.action == "copy":
		return _("Copia al portapapeles: {value}").format(value=action.value)
	if action.action == "broadcast":
		return _("Difusión de Android (no disponible en Windows)")
	return action.action
