"""Controlador principal: une configuración, almacenamiento, red, notificaciones e interfaz."""

import hashlib
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests
import wx

from . import APP_NAME, i18n, paths
from .config import Config, normalize_server
from .formatting import toast_texts
from .i18n import _, ngettext
from .ntfy import actions as ntfy_actions
from .ntfy.models import Message
from .ntfy.publisher import USER_AGENT, auth_header
from .ntfy.subscriber import CONNECTED, EndpointConfig, SubscriptionManager
from .storage import Storage
from .toasts import Notifier, aumid_for, is_aumid_registered, register_aumid, unregister_aumid
from .vault import Vault

log = logging.getLogger(__name__)

BACKLOG_FLUSH_MS = 2000
BACKLOG_INDIVIDUAL_MAX = 3
ICON_MAX_BYTES = 512 * 1024
RETENTION_CHECK_MS = 3600 * 1000


def setup_logging() -> None:
	handler = logging.handlers.RotatingFileHandler(
		paths.log_file(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
	)
	handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
	root = logging.getLogger()
	root.setLevel(logging.INFO)
	root.addHandler(handler)
	logging.getLogger("websockets").setLevel(logging.WARNING)
	logging.getLogger("urllib3").setLevel(logging.WARNING)


def safe_filename(name: str) -> str:
	name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
	return name[:150] or "adjunto"


class Controller:
	def __init__(self, app: wx.App):
		self.app = app
		self.exiting = False
		self.frame = None
		self.tray = None
		self.data_dir = paths.data_dir()
		self.config = Config()
		self.storage = Storage()
		self.vault = Vault()
		self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notificador")
		self._subs = {}
		self._backlog = []
		self._backlog_lock = threading.Lock()
		self._backlog_timer = None
		self._statuses = {}
		self.aumid = aumid_for(paths.base_dir())
		self.notifier = None
		self.manager = SubscriptionManager(self._on_message, self._on_status)

	# Arranque y cierre

	def start(self) -> None:
		from .ui.icon import ensure_icon_file
		from .ui.main_frame import MainFrame
		from .ui.master_password_dialog import unlock_vault
		from .ui.tray import TrayIcon

		self.storage.purge_older_than(int(self.config.get("retention_days") or 0))
		icon_file = ensure_icon_file()
		if self.config.get("register_aumid"):
			register_aumid(self.aumid, APP_NAME, icon_file)
		self._create_notifier()

		if self.vault.exists():
			unlock_vault(None, self.vault)

		self.frame = MainFrame(self)
		self.app.SetTopWindow(self.frame)
		self.tray = TrayIcon(self)
		if not self.config.get("start_minimized"):
			self.frame.Show()
		self.refresh_subscriptions()
		self.frame.update_status_counts()
		self._retention_timer = wx.Timer()
		self._retention_timer.Bind(wx.EVT_TIMER, lambda e: self._purge())
		self._retention_timer.Start(RETENTION_CHECK_MS)

	def _create_notifier(self) -> None:
		registered = self.config.get("register_aumid") and is_aumid_registered(self.aumid)
		self.notifier = Notifier(APP_NAME, self.aumid if registered else None, self.on_toast_activate, wx.CallAfter)

	def _purge(self) -> None:
		days = int(self.config.get("retention_days") or 0)
		if days and self.storage.purge_older_than(days) and self.frame:
			self.frame.reload()

	def exit(self) -> None:
		if self.exiting:
			return
		self.exiting = True
		try:
			self.manager.stop(timeout=3)
		except Exception:  # noqa: BLE001
			log.exception("Error al detener las conexiones")
		self.executor.shutdown(wait=False, cancel_futures=True)
		if self.tray is not None:
			self.tray.RemoveIcon()
			self.tray.Destroy()
			self.tray = None
		if self.frame is not None:
			self.frame.Destroy()
			self.frame = None
		self.storage.close()
		self.app.ExitMainLoop()

	# Suscripciones y conexiones

	def build_endpoint_configs(self) -> list[EndpointConfig]:
		groups: dict[str, EndpointConfig] = {}
		for sub in self.storage.subscriptions():
			if not sub.enabled:
				continue
			server = normalize_server(sub.server)
			config = groups.get(server)
			if config is None:
				config = EndpointConfig(server=server, auth=auth_header(self.vault.get(server)))
				groups[server] = config
			config.topics[sub.topic] = sub.last_time
		return list(groups.values())

	def refresh_subscriptions(self) -> None:
		self._subs = {(s.server, s.topic): s for s in self.storage.subscriptions()}
		self.manager.update(self.build_endpoint_configs())
		if self.frame:
			self.frame.refresh_filters()
			self.frame.reload()
		self._update_connection_summary()

	def add_subscription(self, server, topic, alias="", enabled=True, toasts=True, since_mode="none") -> int:
		server = normalize_server(server)
		sub_id = self.storage.add_subscription(server, topic, alias, enabled, toasts, since_mode)
		now = int(time.time())
		initial = {"none": now, "12h": now - 12 * 3600, "all": 0}.get(since_mode, now)
		if initial:
			self.storage.set_last_seen(server, topic, "", initial)
		self.config.add_recent_server(server, save=False)
		self.config.add_recent_topic(server, topic)
		self.refresh_subscriptions()
		return sub_id

	def update_subscription(self, sub) -> None:
		self.storage.update_subscription(sub)
		self.config.add_recent_server(sub.server, save=False)
		self.config.add_recent_topic(sub.server, sub.topic)
		self.refresh_subscriptions()

	def remove_subscription(self, sub_id: int, delete_messages=False) -> None:
		self.storage.delete_subscription(sub_id, delete_messages)
		self.refresh_subscriptions()

	def credentials_changed(self) -> None:
		self.manager.update(self.build_endpoint_configs())

	def reconnect(self) -> None:
		self.manager.update(self.build_endpoint_configs())
		self.manager.reconnect_all()
		self.set_status(_("Reconectando…"))

	def _on_status(self, status) -> None:
		# Hilo de red
		wx.CallAfter(self._status_ui, status)

	def _status_ui(self, status) -> None:
		if self.exiting:
			return
		self._statuses[status.server] = status
		self._update_connection_summary()

	def _update_connection_summary(self) -> None:
		if not self.frame:
			return
		servers = {c.server for c in self.build_endpoint_configs()}
		if not servers:
			text = _("Sin suscripciones activas")
		else:
			connected = sum(
				1 for s in servers if self._statuses.get(s) is not None and self._statuses[s].state == CONNECTED
			)
			text = _("Conectado a {n} de {total} servidores").format(n=connected, total=len(servers))
		self.frame.set_connection_status(text)

	# Mensajes entrantes (se llaman desde el hilo de red)

	def _on_message(self, msg: Message, backlog: bool) -> None:
		if self.exiting:
			return
		seq = msg.effective_sequence_id
		if msg.event == "message_delete":
			self.storage.apply_delete(msg.server, msg.topic, seq)
			wx.CallAfter(self._after_removal, msg.server, msg.topic, seq)
			return
		if msg.event == "message_clear":
			self.storage.apply_clear(msg.server, msg.topic, seq)
			wx.CallAfter(self._after_removal, msg.server, msg.topic, seq)
			return
		rowid, _replaced = self.storage.add_message(msg)
		if rowid is None:
			return
		self.storage.set_last_seen(msg.server, msg.topic, msg.id, msg.time)
		sub = self._subs.get((normalize_server(msg.server), msg.topic))
		if sub is not None:
			sub.last_time = max(sub.last_time, msg.time)
		wx.CallAfter(self._message_ui, rowid, msg, sub, backlog)

	def _after_removal(self, server, topic, seq) -> None:
		if self.exiting:
			return
		if self.notifier:
			self.notifier.remove(server, topic, seq)
		if self.frame:
			self.frame.reload()

	def _message_ui(self, rowid, msg, sub, backlog) -> None:
		if self.exiting:
			return
		if self.frame:
			self.frame.on_new_messages()
		if not self._should_toast(msg, sub):
			return
		if backlog:
			with self._backlog_lock:
				self._backlog.append((rowid, msg))
			if self._backlog_timer is None:
				self._backlog_timer = wx.CallLater(BACKLOG_FLUSH_MS, self._flush_backlog)
			return
		self._toast(rowid, msg)

	def _should_toast(self, msg, sub) -> bool:
		if not self.config.get("show_toasts") or self.notifier is None:
			return False
		if sub is not None and not sub.toasts:
			return False
		return msg.priority >= int(self.config.get("min_toast_priority") or 1)

	def _flush_backlog(self) -> None:
		self._backlog_timer = None
		with self._backlog_lock:
			pending, self._backlog = self._backlog, []
		if not pending:
			return
		if len(pending) <= BACKLOG_INDIVIDUAL_MAX:
			for rowid, msg in pending:
				self._toast(rowid, msg)
			return
		topics = sorted({m.topic for _r, m in pending})
		title = ngettext("{n} notificación nueva", "{n} notificaciones nuevas", len(pending)).format(n=len(pending))
		self.notifier.show_summary(title, ", ".join(topics))

	def _toast(self, rowid, msg) -> None:
		if msg.icon:
			self.executor.submit(self._toast_with_icon, rowid, msg)
		else:
			self._show_toast(rowid, msg, None)

	def _toast_with_icon(self, rowid, msg) -> None:
		path = self._download_icon(msg.icon)
		wx.CallAfter(self._show_toast, rowid, msg, path)

	def _show_toast(self, rowid, msg, icon_path) -> None:
		if self.exiting or self.notifier is None:
			return
		header, body = toast_texts(msg)
		self.notifier.show(rowid, msg, header, body, icon_path, with_buttons=self.config.get("toast_actions"))

	def _download_icon(self, url: str) -> str | None:
		if not url.lower().startswith(("http://", "https://")):
			return None
		digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
		for ext in (".png", ".jpg"):
			cached = paths.icons_dir() / (digest + ext)
			if cached.exists():
				return str(cached)
		try:
			with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(5, 10), stream=True) as response:
				response.raise_for_status()
				ctype = response.headers.get("Content-Type", "")
				ext = ".png" if "png" in ctype else ".jpg" if ("jpeg" in ctype or "jpg" in ctype) else None
				if ext is None:
					return None
				data = b""
				for chunk in response.iter_content(32 * 1024):
					data += chunk
					if len(data) > ICON_MAX_BYTES:
						return None
			path = paths.icons_dir() / (digest + ext)
			path.write_bytes(data)
			return str(path)
		except (requests.RequestException, OSError):
			log.info("No se pudo descargar el icono %s", url)
			return None

	# Activación desde Windows

	def on_toast_activate(self, rowid, action_index) -> None:
		if self.exiting:
			return
		stored = self.storage.get_message(rowid) if rowid is not None else None
		if action_index is not None and stored is not None:
			self.run_action(self.frame, stored, action_index, from_toast=True)
			return
		self.show_window()
		if self.frame and rowid is not None:
			self.frame.select_rowid(rowid)

	# Acciones sobre notificaciones

	def run_action(self, parent, stored, index: int, from_toast=False) -> None:
		if stored is None or not 0 <= index < len(stored.msg.actions):
			return
		action = stored.msg.actions[index]
		msg = stored.msg
		if action.action == "view":
			self.open_url(parent, action.url)
		elif action.action == "copy":
			self.copy_to_clipboard(action.value)
		elif action.action == "http":
			self.set_status(_("Ejecutando «{label}»…").format(label=action.label))

			def worker():
				try:
					result = ntfy_actions.run_http(action)
					wx.CallAfter(self._action_done, parent, action, result, None, from_toast)
				except ntfy_actions.ActionError as exc:
					wx.CallAfter(self._action_done, parent, action, None, str(exc), from_toast)

			self.executor.submit(worker)
		else:
			self.set_status(_("Esta acción solo está disponible en Android."))
			return
		if action.clear:
			self.storage.mark_read([stored.rowid])
			if self.notifier:
				self.notifier.remove(msg.server, msg.topic, msg.effective_sequence_id)
			if self.frame:
				self.frame.reload()

	def _action_done(self, parent, action, result, problem, from_toast) -> None:
		if self.exiting:
			return
		if problem:
			if from_toast and self.notifier:
				self.notifier.show_summary(_("Error en «{label}»").format(label=action.label), problem)
			else:
				wx.MessageBox(problem, _("Error en la acción"), wx.OK | wx.ICON_ERROR, parent)
			self.set_status(problem)
		else:
			self.set_status(result)
			if from_toast and self.notifier:
				self.notifier.show_summary(action.label, result)

	def open_url(self, parent, url: str) -> None:
		try:
			ntfy_actions.open_url(url)
		except ntfy_actions.ActionError as exc:
			wx.MessageBox(str(exc), _("Error"), wx.OK | wx.ICON_ERROR, parent)

	def copy_to_clipboard(self, text: str) -> None:
		if wx.TheClipboard.Open():
			try:
				wx.TheClipboard.SetData(wx.TextDataObject(text))
				wx.TheClipboard.Flush()
			finally:
				wx.TheClipboard.Close()
			self.set_status(_("Copiado al portapapeles."))
		else:
			self.set_status(_("No se pudo abrir el portapapeles."))

	def attachment_action(self, parent, stored) -> None:
		att = stored.msg.attachment
		if stored.local_attachment and os.path.exists(stored.local_attachment):
			try:
				os.startfile(stored.local_attachment)
			except OSError as exc:
				wx.MessageBox(str(exc), _("Error"), wx.OK | wx.ICON_ERROR, parent)
			return
		if not att or not att.url:
			return
		if att.expires and att.expires < time.time():
			if wx.MessageBox(
				_("El adjunto caducó y probablemente ya no esté disponible en el servidor. ¿Intentar descargarlo?"),
				_("Adjunto caducado"),
				wx.YES_NO | wx.ICON_WARNING,
				parent,
			) != wx.YES:
				return
		name = safe_filename(att.name or os.path.basename(urlsplit(att.url).path) or "adjunto")
		target = paths.attachments_dir() / f"{safe_filename(stored.msg.id)}_{name}"
		headers = {"User-Agent": USER_AGENT}
		server = normalize_server(stored.msg.server)
		if att.url.startswith(server + "/"):
			auth = auth_header(self.vault.get(server))
			if auth:
				headers["Authorization"] = auth
		self.set_status(_("Descargando {name}…").format(name=name))

		def worker():
			try:
				with requests.get(att.url, headers=headers, stream=True, timeout=(10, 120)) as response:
					response.raise_for_status()
					tmp = target.with_name(target.name + ".part")
					with open(tmp, "wb") as fh:
						for chunk in response.iter_content(64 * 1024):
							fh.write(chunk)
					os.replace(tmp, target)
				wx.CallAfter(downloaded, None)
			except (requests.RequestException, OSError) as exc:
				wx.CallAfter(downloaded, str(exc))

		def downloaded(problem):
			if self.exiting:
				return
			if problem:
				self.set_status(problem)
				wx.MessageBox(
					_("No se pudo descargar el adjunto: {error}").format(error=problem),
					_("Error"),
					wx.OK | wx.ICON_ERROR,
					parent,
				)
				return
			self.storage.set_local_attachment(stored.rowid, str(target))
			stored.local_attachment = str(target)
			self.set_status(_("Adjunto descargado en {path}").format(path=target))
			if self.frame:
				self.frame.reload()
				self.frame.show_selected()
			if wx.MessageBox(
				_("Adjunto descargado. ¿Abrirlo ahora?"), _("Adjunto"), wx.YES_NO | wx.ICON_QUESTION, parent
			) == wx.YES:
				try:
					os.startfile(str(target))
				except OSError as exc:
					wx.MessageBox(str(exc), _("Error"), wx.OK | wx.ICON_ERROR, parent)

		self.executor.submit(worker)

	# Ventanas y diálogos

	def set_status(self, text: str) -> None:
		if self.frame:
			self.frame.set_status(text)

	def update_tray(self, unread: int) -> None:
		if self.tray:
			self.tray.set_unread(unread)

	def show_window(self) -> None:
		if self.frame:
			self.frame.bring_to_front()

	def toggle_window(self) -> None:
		if not self.frame:
			return
		if self.frame.IsShown() and self.frame.IsActive():
			self.frame.Hide()
		else:
			self.frame.bring_to_front()

	def show_publish(self, parent, server: str = "", topic: str = "") -> None:
		from .ui.publish_dialog import PublishDialog

		if parent is None:
			self.show_window()
			parent = self.frame
		with PublishDialog(parent, self, server, topic) as dlg:
			dlg.ShowModal()
		if self.frame and self.frame.IsShown():
			self.frame.list.SetFocus()

	def show_subscriptions(self, parent) -> None:
		from .ui.subscriptions_dialog import SubscriptionsDialog

		with SubscriptionsDialog(parent, self) as dlg:
			dlg.ShowModal()

	def show_credentials(self, parent) -> None:
		from .ui.credentials_dialog import CredentialsDialog

		with CredentialsDialog(parent, self) as dlg:
			dlg.ShowModal()

	def show_status(self, parent) -> None:
		from .ui.preferences_dialog import ConnectionStatusDialog

		with ConnectionStatusDialog(parent, self) as dlg:
			dlg.ShowModal()

	def show_preferences(self, parent) -> None:
		from .ui.preferences_dialog import PreferencesDialog

		with PreferencesDialog(parent, self.config) as dlg:
			if dlg.ShowModal() != wx.ID_OK:
				return
			before = self.config.get("register_aumid")
			dlg.apply()
		after = self.config.get("register_aumid")
		if before != after:
			self.register_windows(parent, after, quiet=True)
		self._purge()

	def unlock_credentials(self, parent) -> None:
		from .ui.master_password_dialog import unlock_vault

		if not self.vault.exists():
			wx.MessageBox(_("Todavía no hay credenciales guardadas."), APP_NAME, wx.OK | wx.ICON_INFORMATION, parent)
			return
		if self.vault.unlocked:
			wx.MessageBox(_("Las credenciales ya están desbloqueadas."), APP_NAME, wx.OK | wx.ICON_INFORMATION, parent)
			return
		if unlock_vault(parent, self.vault):
			self.credentials_changed()
			self.set_status(_("Credenciales desbloqueadas."))

	def change_master_password(self, parent) -> None:
		from .ui.master_password_dialog import change_master_password

		if change_master_password(parent, self.vault):
			wx.MessageBox(_("Contraseña maestra actualizada."), APP_NAME, wx.OK | wx.ICON_INFORMATION, parent)

	def register_windows(self, parent, register: bool, quiet=False) -> None:
		from .ui.icon import ensure_icon_file

		if register:
			ok = register_aumid(self.aumid, APP_NAME, ensure_icon_file())
			message = _("El programa se ha registrado en Windows.") if ok else _("No se pudo registrar el programa.")
		else:
			if self.notifier:
				self.notifier.clear_all()
			ok = unregister_aumid(self.aumid)
			message = _("Se ha eliminado el registro de Windows.") if ok else _("No se pudo eliminar el registro.")
		self.config.set("register_aumid", register)
		self._create_notifier()
		if not quiet:
			wx.MessageBox(message, APP_NAME, wx.OK | wx.ICON_INFORMATION, parent)

	def open_data_folder(self) -> None:
		try:
			os.startfile(str(self.data_dir))
		except OSError:
			pass


class NotificadorApp(wx.App):
	def OnInit(self):
		self.SetAppName(APP_NAME)
		# Antes de crear cualquier ventana, para que wx use el mismo idioma que el programa.
		self.locale = i18n.create_wx_locale()
		if not paths.is_data_dir_writable():
			wx.MessageBox(
				_("No se puede escribir en la carpeta de datos:\n{path}\n\nCopia el programa a una carpeta con permisos de escritura.").format(
					path=paths.data_dir()
				),
				APP_NAME,
				wx.OK | wx.ICON_ERROR,
			)
			return False
		name = "notificador-" + hashlib.sha1(str(paths.data_dir()).lower().encode("utf-8")).hexdigest()[:12]
		self.instance = wx.SingleInstanceChecker(name)
		if self.instance.IsAnotherRunning():
			wx.MessageBox(
				_("{name} ya se está ejecutando. Búscalo en el área de notificación.").format(name=APP_NAME),
				APP_NAME,
				wx.OK | wx.ICON_INFORMATION,
			)
			return False
		self.controller = Controller(self)
		self.controller.start()
		return True


def main() -> int:
	paths.data_dir()
	setup_logging()
	config = Config()
	language = i18n.install(config.get("language") or None)
	def excepthook(exc_type, value, tb):
		log.error("Excepción no controlada", exc_info=(exc_type, value, tb))
		sys.__excepthook__(exc_type, value, tb)

	sys.excepthook = excepthook
	log.info("Iniciando %s (idioma: %s)", APP_NAME, language)
	app = NotificadorApp(False)
	app.MainLoop()
	return 0
