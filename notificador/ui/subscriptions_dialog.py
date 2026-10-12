"""Gestión de suscripciones a temas."""

import wx

from ..config import normalize_server
from ..i18n import _
from ..ntfy.models import is_valid_topic
from ..ntfy.subscriber import AUTH_ERROR, CONNECTED, CONNECTING, ERROR, STOPPED, WAITING
from ..storage import SINCE_MODES, Subscription
from .a11y import Form, dialog_buttons, error
from .credentials_dialog import AuthPanel
from .master_password_dialog import ensure_vault


def state_label(state: str | None) -> str:
	return {
		CONNECTING: _("Conectando"),
		CONNECTED: _("Conectado"),
		WAITING: _("Reconectando"),
		ERROR: _("Error"),
		AUTH_ERROR: _("Error de autenticación"),
		STOPPED: _("Detenido"),
		None: _("Sin conexión"),
	}.get(state, state or "")


class SubscriptionDialog(wx.Dialog):
	def __init__(self, parent, controller, sub: Subscription | None = None):
		super().__init__(parent, title=_("Editar suscripción") if sub else _("Nueva suscripción"))
		self.controller = controller
		self.sub = sub
		panel = wx.Panel(self)
		form = Form(panel)
		self.server = form.combo(_("&Servidor:"), controller.config.recent_servers())
		self.topic = form.combo(_("&Tema:"))
		self.alias = form.text(_("&Nombre para mostrar (opcional):"))
		self.enabled = form.check(_("Suscripción &activa"), True)
		self.toasts = form.check(_("Mostrar en el centro de &notificaciones de Windows"), True)
		self.since = form.choice(
			_("Al suscribirse, &recibir:"),
			[_("Solo las nuevas"), _("Las de las últimas 12 horas"), _("Todas las disponibles en el servidor")],
		)
		root = wx.BoxSizer(wx.VERTICAL)
		root.Add(form.sizer, 0, wx.EXPAND | wx.ALL, 10)
		self.auth = AuthPanel(panel)
		root.Add(self.auth, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
		panel.SetSizer(root)
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		outer.Add(dialog_buttons(self), 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(outer)
		self.SetMinSize((500, -1))
		self.Fit()
		self.CentreOnParent()

		if sub:
			self.server.SetValue(sub.server)
			self.topic.SetValue(sub.topic)
			self.alias.SetValue(sub.alias)
			self.enabled.SetValue(sub.enabled)
			self.toasts.SetValue(sub.toasts)
			self.since.SetSelection(SINCE_MODES.index(sub.since_mode) if sub.since_mode in SINCE_MODES else 0)
			self.since.Disable()
		else:
			self.server.SetValue(controller.config.recent_servers()[0])
		self._server = None
		self.on_server_changed()
		self.server.Bind(wx.EVT_COMBOBOX, lambda e: self.on_server_changed())
		self.server.Bind(wx.EVT_KILL_FOCUS, lambda e: (e.Skip(), wx.CallAfter(self.on_server_changed)))
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		(self.topic if not sub else self.alias).SetFocus()

	def on_server_changed(self):
		if not self:
			return
		server = normalize_server(self.server.GetValue())
		if server == self._server:
			return
		self._server = server
		topic = self.topic.GetValue()
		self.topic.Set(self.controller.config.recent_topics(server))
		self.topic.SetValue(topic)
		self.auth.set_credential(self.controller.vault.get(server))

	def on_ok(self, event):
		server = normalize_server(self.server.GetValue())
		topic = self.topic.GetValue().strip()
		if not server:
			error(self, _("Indica un servidor."), self.server)
			return
		if not is_valid_topic(topic):
			error(
				self,
				_("El tema debe tener entre 1 y 64 caracteres: letras, números, guiones o guiones bajos."),
				self.topic,
			)
			return
		existing = self.controller.storage.find_subscription(server, topic)
		if existing and (self.sub is None or existing.id != self.sub.id):
			error(self, _("Ya estás suscrito a ese tema en ese servidor."), self.topic)
			return
		problem = self.auth.validate()
		if problem:
			error(self, problem[0], problem[1])
			return
		cred = self.auth.get_credential()
		if cred and self.auth.wants_save and self.controller.vault.get(server) != cred:
			if not ensure_vault(self, self.controller.vault):
				return
			self.controller.vault.set(server, cred["type"], cred["username"], cred["secret"])
		elif cred is None and self.controller.vault.unlocked and self.controller.vault.get(server):
			self.controller.vault.delete(server)
		self.EndModal(wx.ID_OK)

	def values(self) -> dict:
		return {
			"server": normalize_server(self.server.GetValue()),
			"topic": self.topic.GetValue().strip(),
			"alias": self.alias.GetValue().strip(),
			"enabled": self.enabled.GetValue(),
			"toasts": self.toasts.GetValue(),
			"since_mode": SINCE_MODES[self.since.GetSelection()],
		}


class SubscriptionsDialog(wx.Dialog):
	def __init__(self, parent, controller):
		super().__init__(parent, title=_("Suscripciones"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.controller = controller
		panel = wx.Panel(self)
		root = wx.BoxSizer(wx.VERTICAL)
		root.Add(wx.StaticText(panel, label=_("&Suscripciones:")), 0, wx.LEFT | wx.TOP, 10)
		self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(640, 240))
		self.list.SetName(_("Suscripciones"))
		for i, (title, width) in enumerate(
			[(_("Nombre"), 170), (_("Tema"), 120), (_("Servidor"), 170), (_("Estado"), 130), (_("Avisos"), 60)]
		):
			self.list.InsertColumn(i, title, width=width)
		root.Add(self.list, 1, wx.EXPAND | wx.ALL, 10)
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.add_btn = wx.Button(panel, label=_("&Añadir…"))
		self.edit_btn = wx.Button(panel, label=_("&Editar…"))
		self.toggle_btn = wx.Button(panel, label=_("&Desactivar"))
		self.remove_btn = wx.Button(panel, label=_("E&liminar"))
		for btn in (self.add_btn, self.edit_btn, self.toggle_btn, self.remove_btn):
			row.Add(btn, 0, wx.RIGHT, 6)
		root.Add(row, 0, wx.LEFT | wx.RIGHT, 10)
		panel.SetSizer(root)
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		close = wx.Button(self, wx.ID_CLOSE, _("&Cerrar"))
		outer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
		self.SetEscapeId(wx.ID_CLOSE)
		self.SetSizerAndFit(outer)
		self.CentreOnParent()

		self.add_btn.Bind(wx.EVT_BUTTON, self.on_add)
		self.edit_btn.Bind(wx.EVT_BUTTON, self.on_edit)
		self.toggle_btn.Bind(wx.EVT_BUTTON, self.on_toggle)
		self.remove_btn.Bind(wx.EVT_BUTTON, self.on_remove)
		self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_edit)
		self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self.update_buttons())
		self.list.Bind(wx.EVT_KEY_DOWN, self.on_key)
		close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
		self.subs: list[Subscription] = []
		self.refresh()
		self.list.SetFocus()

	def refresh(self, select_id: int | None = None):
		self.subs = self.controller.storage.subscriptions()
		statuses = {s.server: s.state for s in self.controller.manager.statuses()}
		self.list.DeleteAllItems()
		for i, sub in enumerate(self.subs):
			self.list.InsertItem(i, sub.display_name)
			self.list.SetItem(i, 1, sub.topic)
			self.list.SetItem(i, 2, sub.server)
			state = state_label(statuses.get(sub.server)) if sub.enabled else _("Desactivada")
			self.list.SetItem(i, 3, state)
			self.list.SetItem(i, 4, _("Sí") if sub.toasts else _("No"))
		if self.subs:
			index = next((i for i, s in enumerate(self.subs) if s.id == select_id), 0)
			self.list.Select(index)
			self.list.Focus(index)
		self.update_buttons()

	def selected(self) -> Subscription | None:
		index = self.list.GetFirstSelected()
		return self.subs[index] if 0 <= index < len(self.subs) else None

	def update_buttons(self):
		sub = self.selected()
		self.edit_btn.Enable(sub is not None)
		self.remove_btn.Enable(sub is not None)
		self.toggle_btn.Enable(sub is not None)
		self.toggle_btn.SetLabel(_("&Desactivar") if sub is None or sub.enabled else _("&Activar"))

	def on_key(self, event):
		if event.GetKeyCode() == wx.WXK_DELETE:
			self.on_remove(None)
		else:
			event.Skip()

	def on_add(self, event):
		with SubscriptionDialog(self, self.controller) as dlg:
			if dlg.ShowModal() != wx.ID_OK:
				return
			sub_id = self.controller.add_subscription(**dlg.values())
		self.refresh(sub_id)
		self.list.SetFocus()

	def on_edit(self, event):
		sub = self.selected()
		if not sub:
			return
		with SubscriptionDialog(self, self.controller, sub) as dlg:
			if dlg.ShowModal() != wx.ID_OK:
				return
			values = dlg.values()
		values.pop("since_mode", None)
		for key, value in values.items():
			setattr(sub, key, value)
		self.controller.update_subscription(sub)
		self.refresh(sub.id)
		self.list.SetFocus()

	def on_toggle(self, event):
		sub = self.selected()
		if not sub:
			return
		sub.enabled = not sub.enabled
		self.controller.update_subscription(sub)
		self.refresh(sub.id)
		self.list.SetFocus()

	def on_remove(self, event):
		sub = self.selected()
		if not sub:
			return
		dlg = wx.MessageDialog(
			self,
			_("¿Eliminar la suscripción a {name}? También puedes borrar las notificaciones guardadas de este tema.").format(
				name=sub.display_name
			),
			_("Eliminar suscripción"),
			wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
		)
		dlg.SetYesNoCancelLabels(_("Eliminar y &borrar historial"), _("Eliminar y &conservar historial"), _("Cancelar"))
		answer = dlg.ShowModal()
		dlg.Destroy()
		if answer == wx.ID_CANCEL:
			return
		self.controller.remove_subscription(sub.id, delete_messages=answer == wx.ID_YES)
		self.refresh()
		self.list.SetFocus()
