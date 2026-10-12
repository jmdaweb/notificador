"""Preferencias generales y estado de las conexiones."""

import time

import wx

from .. import i18n
from ..i18n import _
from .a11y import Form, dialog_buttons
from .publish_dialog import priority_labels
from .subscriptions_dialog import state_label


class PreferencesDialog(wx.Dialog):
	def __init__(self, parent, config):
		super().__init__(parent, title=_("Preferencias"))
		self.config = config
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
			_("&Borrar notificaciones con más de estos días (0 = nunca):"),
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
		panel.SetSizer(root)
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		outer.Add(dialog_buttons(self), 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(outer)
		self.CentreOnParent()
		self.show_toasts.SetFocus()

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
		cfg.save()


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
