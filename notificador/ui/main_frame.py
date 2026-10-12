"""Ventana principal: lista de notificaciones, contenido y acciones."""

import wx

from .. import APP_NAME, APP_VERSION
from ..formatting import content_text, list_columns
from ..i18n import _
from ..ntfy.actions import SUPPORTED, describe
from .icon import app_icon_bundle

MARK_READ_DELAY_MS = 1500

COLUMNS = (
	(lambda: _("Título"), 220),
	(lambda: _("Mensaje"), 260),
	(lambda: _("Prioridad"), 80),
	(lambda: _("Tema"), 110),
	(lambda: _("Fecha"), 110),
	(lambda: _("Estado"), 80),
)


class NotificationList(wx.ListCtrl):
	"""Lista virtual en modo informe (el lector de pantalla lee cada columna con su encabezado)."""

	def __init__(self, parent):
		super().__init__(parent, style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.LC_SINGLE_SEL)
		self.items = []
		for i, (title, width) in enumerate(COLUMNS):
			self.InsertColumn(i, title(), width=width)
		self._unread_attr = wx.ItemAttr()
		font = self.GetFont()
		font.SetWeight(wx.FONTWEIGHT_BOLD)
		self._unread_attr.SetFont(font)
		self._cache = {}

	def set_items(self, items):
		self.items = items
		self._cache = {}
		self.SetItemCount(len(items))
		self.Refresh()

	def refresh_item(self, index):
		self._cache.pop(index, None)
		if 0 <= index < len(self.items):
			self.RefreshItem(index)

	def OnGetItemText(self, item, column):
		cols = self._cache.get(item)
		if cols is None:
			if not 0 <= item < len(self.items):
				return ""
			stored = self.items[item]
			cols = list_columns(stored.msg, stored.read)
			self._cache[item] = cols
		return cols[column] if column < len(cols) else ""

	def OnGetItemAttr(self, item):
		if 0 <= item < len(self.items) and not self.items[item].read:
			return self._unread_attr
		return None

	def selected_index(self) -> int:
		return self.GetFirstSelected()


class MainFrame(wx.Frame):
	def __init__(self, controller):
		super().__init__(None, title=APP_NAME, size=(980, 680))
		self.controller = controller
		self.SetIcons(app_icon_bundle())
		self.filters = []
		self._current_rowid = None
		self._shown_rowid = None
		self._mark_timer = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self.on_mark_timer, self._mark_timer)

		self._build_menu()
		panel = wx.Panel(self)
		self.panel = panel
		root = wx.BoxSizer(wx.VERTICAL)

		top = wx.BoxSizer(wx.HORIZONTAL)
		top.Add(wx.StaticText(panel, label=_("&Ver:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
		self.filter = wx.Choice(panel)
		self.filter.SetName(_("Ver"))
		top.Add(self.filter, 1, wx.RIGHT, 12)
		self.unread_only = wx.CheckBox(panel, label=_("Solo &no leídas"))
		top.Add(self.unread_only, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
		top.Add(wx.StaticText(panel, label=_("&Buscar:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
		self.search = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
		self.search.SetName(_("Buscar"))
		top.Add(self.search, 1)
		root.Add(top, 0, wx.EXPAND | wx.ALL, 8)

		split = wx.BoxSizer(wx.HORIZONTAL)
		left = wx.BoxSizer(wx.VERTICAL)
		left.Add(wx.StaticText(panel, label=_("No&tificaciones:")), 0, wx.BOTTOM, 4)
		self.list = NotificationList(panel)
		self.list.SetName(_("Notificaciones"))
		left.Add(self.list, 1, wx.EXPAND)
		split.Add(left, 3, wx.EXPAND | wx.RIGHT, 8)

		right = wx.BoxSizer(wx.VERTICAL)
		right.Add(wx.StaticText(panel, label=_("&Contenido:")), 0, wx.BOTTOM, 4)
		self.content = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_AUTO_URL)
		self.content.SetName(_("Contenido"))
		right.Add(self.content, 1, wx.EXPAND)
		split.Add(right, 2, wx.EXPAND)
		root.Add(split, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

		self.actions_panel = wx.Panel(panel)
		self.actions_panel.SetName(_("Acciones"))
		self.actions_sizer = wx.WrapSizer(wx.HORIZONTAL)
		self.actions_panel.SetSizer(self.actions_sizer)
		root.Add(self.actions_panel, 0, wx.EXPAND | wx.ALL, 8)
		panel.SetSizer(root)

		self.CreateStatusBar(3)
		self.SetStatusWidths([-3, -2, -2])

		self.filter.Bind(wx.EVT_CHOICE, lambda e: self.reload())
		self.unread_only.Bind(wx.EVT_CHECKBOX, lambda e: self.reload())
		self.search.Bind(wx.EVT_TEXT_ENTER, lambda e: self.reload())
		self._search_timer = wx.CallLater(400, self.reload)
		self._search_timer.Stop()
		self.search.Bind(wx.EVT_TEXT, lambda e: self._search_timer.Start(400))
		self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_select)
		self.list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_deselect)
		self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_activate_item)
		self.list.Bind(wx.EVT_CONTEXT_MENU, self.on_context_menu)
		self.list.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
		self.content.Bind(wx.EVT_TEXT_URL, self.on_content_url)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.Bind(wx.EVT_ICONIZE, self.on_iconize)

		self.refresh_filters()
		self.reload()
		self.show_selected()
		self.CentreOnScreen()
		self.list.SetFocus()

	# Menús

	def _build_menu(self):
		bar = wx.MenuBar()
		c = self.controller

		def item(menu, label, handler, help_text=""):
			entry = menu.Append(wx.ID_ANY, label, help_text)
			self.Bind(wx.EVT_MENU, handler, entry)
			return entry

		file_menu = wx.Menu()
		item(file_menu, _("&Publicar notificación…\tCtrl+N"), lambda e: c.show_publish(self))
		item(
			file_menu,
			_("Publicar en el tema &seleccionado…\tCtrl+Shift+N"),
			lambda e: self.publish_to_selected(),
		)
		file_menu.AppendSeparator()
		item(file_menu, _("&Ocultar en el área de notificación\tCtrl+H"), lambda e: self.Hide())
		item(file_menu, _("&Salir\tCtrl+Q"), lambda e: c.exit())
		bar.Append(file_menu, _("&Archivo"))

		notif = wx.Menu()
		item(notif, _("Marcar como &leída\tCtrl+R"), lambda e: self.mark_selected(True))
		item(notif, _("Marcar como &no leída\tCtrl+Shift+R"), lambda e: self.mark_selected(False))
		item(notif, _("Marcar &todas como leídas\tCtrl+Shift+A"), lambda e: self.mark_all_read())
		notif.AppendSeparator()
		item(notif, _("&Copiar mensaje\tCtrl+Shift+C"), lambda e: self.copy_message())
		item(notif, _("Abrir &enlace\tCtrl+L"), lambda e: self.open_click())
		item(notif, _("&Descargar o abrir adjunto\tCtrl+D"), lambda e: self.attachment_action())
		item(notif, _("&Ejecutar acción…\tCtrl+E"), lambda e: self.choose_action())
		notif.AppendSeparator()
		item(notif, _("E&liminar\tDel"), lambda e: self.delete_selected())
		item(notif, _("&Vaciar historial de la vista actual…"), lambda e: self.clear_history())
		notif.AppendSeparator()
		item(notif, _("&Buscar\tCtrl+F"), lambda e: self.search.SetFocus())
		item(notif, _("&Reconectar\tF5"), lambda e: c.reconnect())
		bar.Append(notif, _("&Notificaciones"))

		tools = wx.Menu()
		item(tools, _("&Suscripciones…\tCtrl+U"), lambda e: c.show_subscriptions(self))
		item(tools, _("Ser&vidores y credenciales…\tCtrl+K"), lambda e: c.show_credentials(self))
		item(tools, _("&Estado de las conexiones…\tCtrl+I"), lambda e: c.show_status(self))
		tools.AppendSeparator()
		item(tools, _("&Desbloquear credenciales…"), lambda e: c.unlock_credentials(self))
		item(tools, _("Cambiar contraseña &maestra…"), lambda e: c.change_master_password(self))
		tools.AppendSeparator()
		item(tools, _("Registrar en el &centro de notificaciones de Windows"), lambda e: c.register_windows(self, True))
		item(tools, _("Eliminar el registro de &Windows"), lambda e: c.register_windows(self, False))
		tools.AppendSeparator()
		item(tools, _("&Preferencias…\tCtrl+,"), lambda e: c.show_preferences(self))
		bar.Append(tools, _("&Herramientas"))

		help_menu = wx.Menu()
		item(help_menu, _("&Abrir la carpeta de datos"), lambda e: c.open_data_folder())
		item(help_menu, _("&Acerca de…"), lambda e: self.show_about())
		bar.Append(help_menu, _("A&yuda"))
		self.SetMenuBar(bar)

	# Filtros y recarga

	def refresh_filters(self):
		current = self.current_filter()
		subs = self.controller.storage.subscriptions()
		self.filters = [(None, None)] + [(s.server, s.topic) for s in subs]
		labels = [_("Todas las suscripciones")] + [s.display_name for s in subs]
		self.filter.Set(labels)
		index = self.filters.index(current) if current in self.filters else 0
		self.filter.SetSelection(index)

	def current_filter(self):
		index = self.filter.GetSelection() if self.filters else 0
		if 0 <= index < len(self.filters):
			return self.filters[index]
		return (None, None)

	def reload(self, select_rowid=None):
		server, topic = self.current_filter()
		keep = select_rowid if select_rowid is not None else self._current_rowid
		items = self.controller.storage.query_messages(
			server, topic, self.unread_only.GetValue(), self.search.GetValue().strip()
		)
		self.list.set_items(items)
		index = next((i for i, s in enumerate(items) if s.rowid == keep), -1)
		if index < 0 and items:
			index = 0
		if index >= 0:
			if self.list.GetFirstSelected() != index:
				self.list.Select(index)
			self.list.Focus(index)
		else:
			self._current_rowid = None
			self.show_selected()
		self.update_status_counts()

	def update_status_counts(self):
		unread = self.controller.storage.unread_count()
		total = len(self.list.items)
		self.SetStatusText(_("{total} en la vista, {unread} sin leer").format(total=total, unread=unread), 1)
		self.controller.update_tray(unread)

	def set_status(self, text: str):
		self.SetStatusText(text, 0)

	def set_connection_status(self, text: str):
		self.SetStatusText(text, 2)

	def selected(self):
		index = self.list.selected_index()
		if 0 <= index < len(self.list.items):
			return self.list.items[index]
		return None

	# Selección y contenido

	def on_select(self, event):
		stored = self.selected()
		rowid = stored.rowid if stored else None
		if rowid is not None and rowid == self._current_rowid and self._shown_rowid == rowid:
			# Misma notificación (p. ej. ha cambiado de posición al llegar otra): no reescribir el contenido.
			return
		self._current_rowid = rowid
		self.show_selected()
		self._mark_timer.Stop()
		if stored and not stored.read and self.controller.config.get("mark_read_on_select"):
			self._mark_timer.StartOnce(MARK_READ_DELAY_MS)

	def on_deselect(self, event):
		wx.CallAfter(self._after_deselect)

	def _after_deselect(self):
		if self and self.list.GetFirstSelected() < 0:
			self._current_rowid = None
			self.show_selected()

	def _user_is_reading(self) -> bool:
		focus = wx.Window.FindFocus()
		return self.IsShown() and self.IsActive() and focus in (self.list, self.content)

	def on_mark_timer(self, event):
		stored = self.selected()
		if stored and not stored.read and stored.rowid == self._current_rowid:
			if not self._user_is_reading():
				# Solo se marca como leída cuando el usuario está mirando la notificación.
				self._mark_timer.StartOnce(MARK_READ_DELAY_MS)
				return
			self.controller.storage.mark_read([stored.rowid])
			stored.read = True
			self.list.refresh_item(self.list.selected_index())
			self.update_status_counts()

	def show_selected(self):
		stored = self.selected()
		self._shown_rowid = stored.rowid if stored else None
		if stored is None:
			self.content.SetValue(_("No hay ninguna notificación seleccionada."))
		else:
			self.content.SetValue(content_text(stored.msg, stored.local_attachment))
			self.content.SetInsertionPoint(0)
		self.rebuild_actions(stored)

	def rebuild_actions(self, stored):
		focus = wx.Window.FindFocus()
		had_focus = focus is not None and focus.GetParent() is self.actions_panel
		self.actions_panel.Freeze()
		self.actions_panel.DestroyChildren()
		self.actions_sizer.Clear()
		if stored is not None:
			msg = stored.msg
			for index, action in enumerate(msg.actions):
				label = action.label or action.action
				if action.action not in SUPPORTED:
					label = _("{label} (solo Android)").format(label=label)
				btn = wx.Button(self.actions_panel, label=label.replace("&", "&&"))
				btn.SetToolTip(describe(action))
				btn.Enable(action.action in SUPPORTED)
				btn.Bind(wx.EVT_BUTTON, lambda e, i=index: self.controller.run_action(self, self.selected(), i))
				self.actions_sizer.Add(btn, 0, wx.RIGHT | wx.BOTTOM, 6)
			if msg.click:
				btn = wx.Button(self.actions_panel, label=_("Abrir &enlace"))
				btn.SetToolTip(msg.click)
				btn.Bind(wx.EVT_BUTTON, lambda e: self.open_click())
				self.actions_sizer.Add(btn, 0, wx.RIGHT | wx.BOTTOM, 6)
			if msg.attachment and msg.attachment.url:
				label = _("Abrir &adjunto") if stored.local_attachment else _("Descargar &adjunto")
				btn = wx.Button(self.actions_panel, label=label)
				btn.SetToolTip(msg.attachment.name or msg.attachment.url)
				btn.Bind(wx.EVT_BUTTON, lambda e: self.attachment_action())
				self.actions_sizer.Add(btn, 0, wx.RIGHT | wx.BOTTOM, 6)
			btn = wx.Button(self.actions_panel, label=_("Co&piar mensaje"))
			btn.Bind(wx.EVT_BUTTON, lambda e: self.copy_message())
			self.actions_sizer.Add(btn, 0, wx.RIGHT | wx.BOTTOM, 6)
		self.actions_panel.Thaw()
		self.panel.Layout()
		if had_focus:
			self.list.SetFocus()

	def on_activate_item(self, event):
		stored = self.selected()
		if stored is None:
			return
		self.content.SetFocus()

	def on_list_key(self, event):
		key = event.GetKeyCode()
		if key == wx.WXK_DELETE:
			self.delete_selected()
		else:
			event.Skip()

	def on_content_url(self, event):
		if event.GetMouseEvent().LeftUp():
			url = self.content.GetRange(event.GetURLStart(), event.GetURLEnd())
			self.controller.open_url(self, url)
		else:
			event.Skip()

	def on_context_menu(self, event):
		stored = self.selected()
		if stored is None:
			return
		menu = wx.Menu()

		def add(label, handler, enabled=True):
			entry = menu.Append(wx.ID_ANY, label)
			entry.Enable(enabled)
			self.Bind(wx.EVT_MENU, handler, entry)

		msg = stored.msg
		for index, action in enumerate(msg.actions):
			add(
				action.label.replace("&", "&&"),
				lambda e, i=index: self.controller.run_action(self, self.selected(), i),
				action.action in SUPPORTED,
			)
		if msg.actions:
			menu.AppendSeparator()
		add(_("Abrir &enlace"), lambda e: self.open_click(), bool(msg.click))
		add(_("Descargar o abrir &adjunto"), lambda e: self.attachment_action(), bool(msg.attachment))
		add(_("&Copiar mensaje"), lambda e: self.copy_message())
		menu.AppendSeparator()
		if stored.read:
			add(_("Marcar como &no leída"), lambda e: self.mark_selected(False))
		else:
			add(_("Marcar como &leída"), lambda e: self.mark_selected(True))
		add(_("&Publicar en este tema…"), lambda e: self.publish_to_selected())
		add(_("E&liminar"), lambda e: self.delete_selected())
		self.PopupMenu(menu)
		menu.Destroy()

	# Operaciones

	def mark_selected(self, read: bool):
		stored = self.selected()
		if stored is None:
			return
		self._mark_timer.Stop()
		self.controller.storage.mark_read([stored.rowid], read)
		stored.read = read
		self.list.refresh_item(self.list.selected_index())
		self.update_status_counts()
		self.set_status(_("Marcada como leída.") if read else _("Marcada como no leída."))

	def mark_all_read(self):
		server, topic = self.current_filter()
		self.controller.storage.mark_all_read(server, topic)
		self.reload()
		self.set_status(_("Todas las notificaciones marcadas como leídas."))

	def delete_selected(self):
		stored = self.selected()
		if stored is None:
			return
		index = self.list.selected_index()
		self.controller.storage.delete_messages([stored.rowid])
		items = self.list.items
		next_row = None
		if index + 1 < len(items):
			next_row = items[index + 1].rowid
		elif index > 0:
			next_row = items[index - 1].rowid
		self._current_rowid = next_row
		self.reload(next_row)
		self.set_status(_("Notificación eliminada."))

	def clear_history(self):
		server, topic = self.current_filter()
		label = self.filter.GetStringSelection()
		if wx.MessageBox(
			_("¿Borrar todas las notificaciones guardadas de «{view}»? Esta acción no se puede deshacer.").format(
				view=label
			),
			_("Vaciar historial"),
			wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
			self,
		) != wx.YES:
			return
		self.controller.storage.clear_history(server, topic)
		self.reload()

	def copy_message(self):
		stored = self.selected()
		if stored is None:
			return
		text = stored.msg.message
		if stored.msg.title:
			text = f"{stored.msg.title}\n{text}"
		self.controller.copy_to_clipboard(text)

	def open_click(self):
		stored = self.selected()
		if stored and stored.msg.click:
			self.controller.open_url(self, stored.msg.click)

	def attachment_action(self):
		stored = self.selected()
		if stored and stored.msg.attachment:
			self.controller.attachment_action(self, stored)

	def choose_action(self):
		stored = self.selected()
		if stored is None or not stored.msg.actions:
			self.set_status(_("La notificación seleccionada no tiene acciones."))
			return
		labels = [a.label for a in stored.msg.actions]
		dlg = wx.SingleChoiceDialog(self, _("Elige la acción que quieres ejecutar:"), _("Ejecutar acción"), labels)
		if dlg.ShowModal() == wx.ID_OK:
			self.controller.run_action(self, stored, dlg.GetSelection())
		dlg.Destroy()

	def publish_to_selected(self):
		stored = self.selected()
		server, topic = self.current_filter()
		if stored is not None:
			server, topic = stored.msg.server, stored.msg.topic
		self.controller.show_publish(self, server or "", topic or "")

	def select_rowid(self, rowid):
		"""Selecciona una notificación concreta (p. ej. al pulsar un toast)."""
		if rowid is None:
			return
		if not any(s.rowid == rowid for s in self.list.items):
			self.filter.SetSelection(0)
			self.unread_only.SetValue(False)
			self.search.SetValue("")
			self._search_timer.Stop()
		self._current_rowid = rowid
		self.reload(rowid)
		self.list.SetFocus()

	def on_new_messages(self):
		self.reload()

	def show_about(self):
		wx.MessageBox(
			_("{name} {version}\n\nCliente accesible para servidores ntfy.\nLos datos se guardan en: {path}").format(
				name=APP_NAME, version=APP_VERSION, path=str(self.controller.data_dir)
			),
			_("Acerca de {name}").format(name=APP_NAME),
			wx.OK | wx.ICON_INFORMATION,
			self,
		)

	# Ventana

	def bring_to_front(self):
		if self.IsIconized():
			self.Iconize(False)
		if not self.IsShown():
			self.Show()
		self.Raise()
		self.RequestUserAttention()
		self.list.SetFocus()

	def on_iconize(self, event):
		if event.IsIconized() and self.controller.config.get("close_to_tray"):
			wx.CallAfter(self.Hide)
		event.Skip()

	def on_close(self, event):
		if event.CanVeto() and self.controller.config.get("close_to_tray") and not self.controller.exiting:
			event.Veto()
			self.Hide()
			return
		self.controller.exit()
