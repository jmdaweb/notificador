"""Notificaciones del sistema (centro de notificaciones de Windows).

Para que los botones de las notificaciones lleguen a la aplicación, Windows necesita un
AppUserModelID registrado; se registra en HKCU\\Software\\Classes\\AppUserModelId\\<AUMID>.
Si WinRT no está disponible se recurre a wx.adv.NotificationMessage (sin botones).
"""

import hashlib
import logging
import sys
from pathlib import Path

from .i18n import _
from .ntfy.actions import SUPPORTED

log = logging.getLogger(__name__)

try:
	from windows_toasts import (
		AudioSource,
		InteractableWindowsToaster,
		Toast,
		ToastAudio,
		ToastButton,
		ToastDisplayImage,
		ToastDuration,
		ToastImagePosition,
		ToastScenario,
	)

	HAVE_WINRT = True
except Exception:  # noqa: BLE001 - cualquier fallo de importación de WinRT
	HAVE_WINRT = False

try:
	import winreg
except ImportError:  # pragma: no cover - solo Windows
	winreg = None

REG_PATH = "Software\\Classes\\AppUserModelId\\{aumid}"
MAX_TOAST_TEXT = 600
MAX_TOAST_BUTTONS = 3


def aumid_for(base: Path) -> str:
	"""AUMID estable para esta copia del programa (depende de la carpeta)."""
	digest = hashlib.sha1(str(base).lower().encode("utf-8")).hexdigest()[:10]
	return f"Notificador.ntfy.{digest}"


def register_aumid(aumid: str, display_name: str, icon: Path | None = None) -> bool:
	if winreg is None:
		return False
	try:
		with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH.format(aumid=aumid)) as key:
			winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
			if icon is not None and icon.exists():
				winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon.resolve()))
		return True
	except OSError:
		log.exception("No se pudo registrar el AUMID")
		return False


def unregister_aumid(aumid: str) -> bool:
	if winreg is None:
		return False
	try:
		winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_PATH.format(aumid=aumid))
		return True
	except FileNotFoundError:
		return True
	except OSError:
		log.exception("No se pudo eliminar el AUMID")
		return False


def is_aumid_registered(aumid: str) -> bool:
	if winreg is None:
		return False
	try:
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH.format(aumid=aumid)):
			return True
	except OSError:
		return False


def priority_options(priority: int) -> dict:
	"""Traduce la prioridad de ntfy a opciones de notificación de Windows.

	1 (mínima): silenciosa y sin ventana emergente (solo centro de notificaciones).
	2 (baja): silenciosa.
	3 (normal): comportamiento por defecto.
	4 (alta): permanece más tiempo en pantalla y con sonido de recordatorio.
	5 (urgente): escenario urgente (atraviesa «No molestar» si el usuario lo permite) y sonido de alarma.
	"""
	priority = min(5, max(1, int(priority or 3)))
	options = {"silent": False, "suppress_popup": False, "long": False, "scenario": "default", "sound": "default"}
	if priority == 1:
		options.update(silent=True, suppress_popup=True)
	elif priority == 2:
		options.update(silent=True)
	elif priority == 4:
		options.update(long=True, sound="reminder")
	elif priority == 5:
		options.update(long=True, scenario="urgent", sound="alarm")
	return options


def toast_tag(server: str, topic: str, sequence_id: str) -> tuple[str, str]:
	"""Etiqueta y grupo del toast (máx. 64 caracteres). Mismo sequence_id ⇒ mismo toast."""
	group = hashlib.sha1(f"{server}/{topic}".encode("utf-8")).hexdigest()[:32]
	tag = hashlib.sha1(f"{server}/{topic}/{sequence_id}".encode("utf-8")).hexdigest()[:32]
	return tag, group


def _truncate(text: str, limit: int) -> str:
	text = text.strip()
	return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_arguments(arguments: str) -> dict:
	result = {}
	for part in (arguments or "").split(";"):
		if "=" in part:
			key, value = part.split("=", 1)
			result[key] = value
	return result


class Notifier:
	"""Muestra notificaciones. on_activate(rowid, action_index | None) se invoca en el hilo de wx."""

	def __init__(self, app_name: str, aumid: str | None, on_activate, call_after):
		self.app_name = app_name
		self.aumid = aumid
		self.on_activate = on_activate
		self.call_after = call_after
		self.toaster = None
		self.interactive = False
		self._tags: dict[str, int] = {}
		if HAVE_WINRT and sys.platform == "win32":
			try:
				self.toaster = InteractableWindowsToaster(app_name, notifierAUMID=aumid)
				self.interactive = aumid is not None
			except Exception:  # noqa: BLE001
				log.exception("No se pudo inicializar WinRT; se usarán notificaciones básicas")
				self.toaster = None

	def show(self, rowid: int, msg, header: str, body: str, icon_path: str | None = None, with_buttons=True) -> None:
		if self.toaster is None:
			self._show_fallback(header, body, msg.priority)
			return
		opts = priority_options(msg.priority)
		tag, group = toast_tag(msg.server, msg.topic, msg.effective_sequence_id)
		host = msg.server.split("://", 1)[-1]
		toast = Toast(text_fields=[_truncate(header, 200), _truncate(body, MAX_TOAST_TEXT)])
		toast.tag = tag
		toast.group = group
		toast.attribution_text = f"{msg.topic} · {host}"
		if opts["long"]:
			toast.duration = ToastDuration.Long
		if opts["scenario"] == "urgent":
			toast.scenario = ToastScenario.Important
		toast.suppress_popup = opts["suppress_popup"]
		if opts["silent"]:
			toast.audio = ToastAudio(silent=True)
		elif opts["sound"] == "reminder":
			toast.audio = ToastAudio(AudioSource.Reminder)
		elif opts["sound"] == "alarm":
			toast.audio = ToastAudio(AudioSource.Alarm)
		if icon_path:
			try:
				toast.AddImage(ToastDisplayImage.fromPath(icon_path, position=ToastImagePosition.AppLogo))
			except Exception:  # noqa: BLE001
				log.debug("No se pudo añadir el icono %s", icon_path)
		if msg.click:
			toast.launch_action = msg.click
		if self.interactive and with_buttons:
			count = 0
			for index, action in enumerate(msg.actions):
				if action.action not in SUPPORTED or count >= MAX_TOAST_BUTTONS:
					continue
				if action.action == "view" and not action.clear and action.url:
					# Activación por protocolo: funciona aunque el programa esté cerrado.
					toast.AddAction(ToastButton(action.label, launch=action.url))
				else:
					toast.AddAction(ToastButton(action.label, arguments=f"row={rowid};a={index}"))
				count += 1
		self._tags[tag] = rowid
		toast.on_activated = lambda args, row=rowid: self._activated(row, args)
		try:
			self.toaster.show_toast(toast)
		except Exception:  # noqa: BLE001
			log.exception("No se pudo mostrar la notificación de Windows")
			self._show_fallback(header, body, msg.priority)

	def _activated(self, rowid: int, args) -> None:
		parsed = parse_arguments(getattr(args, "arguments", "") or "")
		index = parsed.get("a")
		if "row" in parsed:
			try:
				rowid = int(parsed["row"])
			except ValueError:
				pass
		action_index = int(index) if index is not None and index.isdigit() else None
		self.call_after(self.on_activate, rowid, action_index)

	def remove(self, server: str, topic: str, sequence_id: str) -> None:
		if self.toaster is None:
			return
		tag, group = toast_tag(server, topic, sequence_id)
		try:
			dummy = Toast()
			dummy.tag = tag
			dummy.group = group
			self.toaster.remove_toast(dummy)
		except Exception:  # noqa: BLE001
			log.debug("No se pudo retirar la notificación %s", tag)

	def clear_all(self) -> None:
		if self.toaster is None:
			return
		try:
			self.toaster.clear_toasts()
		except Exception:  # noqa: BLE001
			log.debug("No se pudieron retirar las notificaciones")

	def show_summary(self, title: str, body: str) -> None:
		if self.toaster is None:
			self._show_fallback(title, body, 3)
			return
		toast = Toast(text_fields=[title, body])
		toast.on_activated = lambda args: self.call_after(self.on_activate, None, None)
		try:
			self.toaster.show_toast(toast)
		except Exception:  # noqa: BLE001
			log.exception("No se pudo mostrar el resumen")

	def _show_fallback(self, title: str, body: str, priority: int) -> None:
		try:
			import wx
			import wx.adv

			flags = wx.ICON_WARNING if priority >= 4 else wx.ICON_INFORMATION
			note = wx.adv.NotificationMessage(_truncate(title, 200), _truncate(body, MAX_TOAST_TEXT), None, flags)
			note.Show(wx.adv.NotificationMessage.Timeout_Auto)
		except Exception:  # noqa: BLE001
			log.exception("No se pudo mostrar la notificación")
