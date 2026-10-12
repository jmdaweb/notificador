"""Icono en el área de notificación."""

import wx
import wx.adv

from .. import APP_NAME
from ..i18n import _
from .icon import tray_icon


class TrayIcon(wx.adv.TaskBarIcon):
	def __init__(self, controller):
		super().__init__()
		self.controller = controller
		self.icon = tray_icon()
		self.set_unread(0)
		self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, lambda e: self.controller.toggle_window())

	def set_unread(self, unread: int):
		if unread:
			tooltip = _("{name}: {n} sin leer").format(name=APP_NAME, n=unread)
		else:
			tooltip = APP_NAME
		self.SetIcon(self.icon, tooltip)

	def CreatePopupMenu(self):
		menu = wx.Menu()
		frame = self.controller.frame
		label = _("&Ocultar ventana") if frame and frame.IsShown() else _("&Mostrar ventana")
		entries = [
			(label, lambda e: self.controller.toggle_window()),
			(_("&Publicar notificación…"), lambda e: self.controller.show_publish(None)),
			(_("&Reconectar"), lambda e: self.controller.reconnect()),
			(None, None),
			(_("&Salir"), lambda e: self.controller.exit()),
		]
		for text, handler in entries:
			if text is None:
				menu.AppendSeparator()
				continue
			item = menu.Append(wx.ID_ANY, text)
			self.Bind(wx.EVT_MENU, handler, item)
		return menu
