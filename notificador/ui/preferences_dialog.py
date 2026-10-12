"""Preferencias generales y estado de las conexiones."""

import threading
import time

import wx

from .. import i18n, net
from ..i18n import _
from ..ntfy.publisher import USER_AGENT
from .a11y import Form, dialog_buttons, error, strip_mnemonic
from .master_password_dialog import ensure_vault
from .publish_dialog import priority_labels
from .subscriptions_dialog import state_label


class PreferencesDialog(wx.Dialog):
	def __init__(self, parent, controller):
		super().__init__(parent, title=_("Preferencias"))
		self.controller = controller
		self.config = config = controller.config
		self.vault = controller.vault
		panel = wx.Panel(self)
		form = Form(panel)
		self.show_toasts = form.check(
			_("Mostrar las notificaciones en el &centro de notificaciones de Windows"), config.get("show_toasts")
		)
		self.toast_actions = form.check(
			_("Incluir los &botones de acción en las notificaciones de Windows"), config.get("toast_actions")
		)
		self.min_priority = form.choice(
			_("&Prioridad mínima para avisar:"), priority_labels(), int(config.get("min_toast_priority")) - 1
		)
		self.mark_read = form.check(
			_("Marcar como &leída al seleccionar una notificación"), config.get("mark_read_on_select")
		)
		self.close_to_tray = form.check(
			_("Al cerrar la ventana, seguir en el á&rea de notificación"), config.get("close_to_tray")
		)
		self.start_minimized = form.check(_("&Iniciar minimizado"), config.get("start_minimized"))
		self.register_aumid = form.check(
			_("Registrar el programa en Windows para los botones de las notificaciones (clave en HKCU)"),
			config.get("register_aumid"),
		)
		self.retention = form.spin(
			_("Borrar &notificaciones con más de estos días (0 = nunca):"),
			int(config.get("retention_days")),
			0,
			3650,
		)
		# El primer elemento ("") sigue el idioma del sistema; el resto son idiomas concretos.
		self.languages = [""] + i18n.available_languages()
		labels = [_("Idioma del sistema ({name})").format(name=i18n.language_name(i18n.resolve(None)))]
		labels += [i18n.language_name(code) for code in self.languages[1:]]
		current = config.get("language") or ""
		self.language = form.choice(
			_("I&dioma (se aplica al reiniciar):"),
			labels,
			self.languages.index(current) if current in self.languages else 0,
		)
		root = wx.BoxSizer(wx.VERTICAL)
		root.Add(form.sizer, 0, wx.EXPAND | wx.ALL, 10)
		root.Add(self._build_proxy(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		panel.SetSizer(root)
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		outer.Add(dialog_buttons(self), 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(outer)
		self.CentreOnParent()
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		self.show_toasts.SetFocus()

	# Proxy

	def _build_proxy(self, panel) -> wx.Sizer:
		stored = self.config.get("proxy") or {}
		saved = net.ProxySettings.from_config(stored, self.vault.get_secret(net.VAULT_SECRET))
		# Hay una contraseña guardada que no se puede leer porque el almacén está bloqueado.
		self._password_unknown = bool(stored.get("has_password")) and not self.vault.unlocked
		box = wx.StaticBox(panel, label=_("Servidor proxy"))
		sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
		form = Form(box)
		self.proxy_type = form.choice(
			_("&Tipo de proxy:"),
			[_("Configuración del sistema"), _("Sin proxy"), "HTTP", "HTTPS", "SOCKS4", "SOCKS5"],
			net.PROXY_TYPES.index(saved.type),
		)
		self.proxy_host = form.text(_("&Servidor (host):"), saved.host)
		self._port_label = wx.StaticText(box, label=_("P&uerto:"))
		form.sizer.Add(self._port_label, 0, wx.ALIGN_CENTER_VERTICAL)
		self.proxy_port = wx.TextCtrl(box, value=str(saved.port) if saved.port else "")
		form.sizer.Add(self.proxy_port, 1, wx.EXPAND)
		self.proxy_user = form.text(_("Usuari&o (opcional):"), saved.username)
		password_label = _("Contr&aseña (opcional):")
		if self._password_unknown:
			password_label = _("Contr&aseña (opcional; hay una guardada, pero las credenciales están bloqueadas):")
		self.proxy_password = form.password(password_label, saved.password)
		sizer.Add(form.sizer, 0, wx.EXPAND | wx.ALL, 6)
		self.proxy_test = wx.Button(box, label=_("Probar pro&xy"))
		sizer.Add(self.proxy_test, 0, wx.ALL, 6)
		self.proxy_type.Bind(wx.EVT_CHOICE, lambda e: self.update_proxy_state())
		self.proxy_test.Bind(wx.EVT_BUTTON, self.on_test_proxy)
		self.update_proxy_state()
		return sizer

	@property
	def proxy_kind(self) -> str:
		return net.PROXY_TYPES[self.proxy_type.GetSelection()]

	def update_proxy_state(self):
		kind = self.proxy_kind
		manual = kind in net.MANUAL_TYPES
		for ctrl in (self.proxy_host, self.proxy_port, self.proxy_user, self.proxy_password):
			ctrl.Enable(manual)
		if manual:
			label = _("P&uerto (vacío = {port}):").format(port=net.DEFAULT_PORTS[kind])
		else:
			label = _("P&uerto:")
		self._port_label.SetLabel(label)
		self.proxy_port.SetName(strip_mnemonic(label))
		self.Layout()

	def _proxy_from_fields(self):
		"""Configuración escrita en el diálogo, o (mensaje de error, control) si no es válida."""
		port_text = self.proxy_port.GetValue().strip()
		if port_text and not (port_text.isdigit() and 1 <= int(port_text) <= 65535):
			return _("El puerto debe ser un número entre 1 y 65535."), self.proxy_port
		password = self.proxy_password.GetValue()
		if not password and self._password_unknown:
			password = self.vault.get_secret(net.VAULT_SECRET)
		settings = net.ProxySettings(
			type=self.proxy_kind,
			host=self.proxy_host.GetValue().strip(),
			port=int(port_text) if port_text else 0,
			username=self.proxy_user.GetValue().strip(),
			password=password,
		)
		if settings.validate() == "missing_host":
			return _("Escribe el nombre o la dirección IP del servidor proxy."), self.proxy_host
		return settings

	def on_test_proxy(self, event):
		settings = self._proxy_from_fields()
		if isinstance(settings, tuple):
			error(self, *settings)
			return
		url = f"{self.config.recent_servers()[0]}/v1/health"
		self.proxy_test.Disable()
		self.proxy_test.SetLabel(_("Probando…"))

		def worker():
			try:
				status = net.check(settings, url, USER_AGENT)
				result = (status < 500, _("Respuesta de {url}: HTTP {status}.").format(url=url, status=status))
			except Exception as exc:  # noqa: BLE001 - se muestra al usuario
				result = (False, _("Prueba con {url}:\n{error}").format(url=url, error=net.describe_error(exc)))
			wx.CallAfter(done, *result)

		def done(ok, message):
			if not self:
				return
			self.proxy_test.SetLabel(_("Probar pro&xy"))
			self.proxy_test.Enable()
			wx.MessageBox(
				message,
				_("Prueba correcta") if ok else _("Prueba fallida"),
				wx.OK | (wx.ICON_INFORMATION if ok else wx.ICON_ERROR),
				self,
			)
			self.proxy_test.SetFocus()

		threading.Thread(target=worker, name="proxy-test", daemon=True).start()

	def on_ok(self, event):
		settings = self._proxy_from_fields()
		if isinstance(settings, tuple):
			error(self, *settings)
			return
		password = self.proxy_password.GetValue()
		if settings.manual and password and password != self.vault.get_secret(net.VAULT_SECRET):
			if not ensure_vault(self, self.vault):
				error(
					self,
					_("La contraseña del proxy se guarda cifrada y necesita la contraseña maestra."),
					self.proxy_password,
				)
				return
		self.EndModal(wx.ID_OK)

	def apply(self) -> None:
		cfg = self.config
		cfg.set("show_toasts", self.show_toasts.GetValue(), save=False)
		cfg.set("toast_actions", self.toast_actions.GetValue(), save=False)
		cfg.set("min_toast_priority", self.min_priority.GetSelection() + 1, save=False)
		cfg.set("mark_read_on_select", self.mark_read.GetValue(), save=False)
		cfg.set("close_to_tray", self.close_to_tray.GetValue(), save=False)
		cfg.set("start_minimized", self.start_minimized.GetValue(), save=False)
		cfg.set("register_aumid", self.register_aumid.GetValue(), save=False)
		cfg.set("retention_days", self.retention.GetValue(), save=False)
		cfg.set("language", self.languages[self.language.GetSelection()], save=False)
		self._apply_proxy()
		cfg.save()

	def _apply_proxy(self) -> None:
		settings = self._proxy_from_fields()
		if isinstance(settings, tuple):
			return
		password = self.proxy_password.GetValue()
		has_password = bool((self.config.get("proxy") or {}).get("has_password"))
		if settings.manual and self.vault.unlocked:
			# El secreto solo se modifica si el usuario ha podido verlo y editarlo.
			if password:
				if password != self.vault.get_secret(net.VAULT_SECRET):
					self.vault.set_secret(net.VAULT_SECRET, password)
				has_password = True
			elif not self._password_unknown:
				if has_password:
					self.vault.delete_secret(net.VAULT_SECRET)
				has_password = False
		data = settings.to_config()
		data["has_password"] = has_password
		self.config.set("proxy", data, save=False)


class ConnectionStatusDialog(wx.Dialog):
	def __init__(self, parent, controller):
		super().__init__(parent, title=_("Estado de las conexiones"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.controller = controller
		panel = wx.Panel(self)
		root = wx.BoxSizer(wx.VERTICAL)
		root.Add(wx.StaticText(panel, label=_("&Conexiones:")), 0, wx.LEFT | wx.TOP, 10)
		self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(640, 200))
		self.list.SetName(_("Conexiones"))
		for i, (title, width) in enumerate(
			[(_("Servidor"), 180), (_("Estado"), 130), (_("Transporte"), 90), (_("Temas"), 140), (_("Detalle"), 200)]
		):
			self.list.InsertColumn(i, title, width=width)
		root.Add(self.list, 1, wx.EXPAND | wx.ALL, 10)
		row = wx.BoxSizer(wx.HORIZONTAL)
		refresh = wx.Button(panel, label=_("&Actualizar"))
		reconnect = wx.Button(panel, label=_("&Reconectar todo"))
		row.Add(refresh, 0, wx.RIGHT, 6)
		row.Add(reconnect, 0)
		root.Add(row, 0, wx.LEFT | wx.RIGHT, 10)
		panel.SetSizer(root)
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		close = wx.Button(self, wx.ID_CLOSE, _("&Cerrar"))
		outer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
		self.SetEscapeId(wx.ID_CLOSE)
		self.SetSizerAndFit(outer)
		self.CentreOnParent()
		refresh.Bind(wx.EVT_BUTTON, lambda e: self.refresh())
		reconnect.Bind(wx.EVT_BUTTON, self.on_reconnect)
		close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
		self.refresh()
		self.list.SetFocus()

	def refresh(self):
		self.list.DeleteAllItems()
		for i, status in enumerate(sorted(self.controller.manager.statuses(), key=lambda s: s.server)):
			self.list.InsertItem(i, status.server)
			self.list.SetItem(i, 1, state_label(status.state))
			self.list.SetItem(i, 2, status.transport)
			self.list.SetItem(i, 3, ", ".join(status.topics))
			detail = status.detail
			if status.since:
				detail = (detail + " · " if detail else "") + time.strftime("%H:%M:%S", time.localtime(status.since))
			self.list.SetItem(i, 4, detail)
		if self.list.GetItemCount():
			self.list.Select(0)
			self.list.Focus(0)

	def on_reconnect(self, event):
		self.controller.reconnect()
		wx.CallLater(1500, self.refresh)
