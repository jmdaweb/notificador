"""Diálogo completo para publicar notificaciones en un tema."""

import os
import threading

import wx

from ..config import DEFAULT_SERVER, normalize_server
from ..i18n import _
from ..ntfy import publisher
from ..ntfy.models import MAX_ACTIONS, PublishRequest
from .a11y import Form, confirm, error, info
from .action_dialog import ActionDialog, type_labels
from .credentials_dialog import AuthPanel
from .master_password_dialog import ensure_vault

def priority_labels():
	return [
		_("1 - Mínima"),
		_("2 - Baja"),
		_("3 - Normal"),
		_("4 - Alta"),
		_("5 - Urgente"),
	]


TEMPLATE_VALUES = ("", "yes", "github", "grafana", "alertmanager")

ATTACH_NONE, ATTACH_FILE, ATTACH_URL = 0, 1, 2


class PublishDialog(wx.Dialog):
	def __init__(self, parent, controller, server: str = "", topic: str = ""):
		super().__init__(
			parent, title=_("Publicar notificación"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
		)
		self.controller = controller
		self.config = controller.config
		self.actions = []
		self.limits = None
		self._limits_server = None
		self._sending = False

		outer = wx.BoxSizer(wx.VERTICAL)
		self.book = wx.Notebook(self)
		self.book.SetName(_("Secciones"))
		self._build_general()
		self._build_attachment()
		self._build_actions()
		self._build_delivery()
		self._build_auth()
		outer.Add(self.book, 1, wx.EXPAND | wx.ALL, 8)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		self.send_btn = wx.Button(self, wx.ID_OK, _("&Enviar"))
		self.send_btn.SetDefault()
		cancel = wx.Button(self, wx.ID_CANCEL, _("Cerrar"))
		buttons.AddStretchSpacer()
		buttons.Add(self.send_btn, 0, wx.RIGHT, 6)
		buttons.Add(cancel, 0)
		outer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
		self.SetAffirmativeId(wx.ID_NONE)
		self.SetEscapeId(wx.ID_CANCEL)
		self.SetSizerAndFit(outer)
		self.SetMinSize((620, 560))
		self.SetSize((680, 620))
		self.CentreOnParent()

		self.send_btn.Bind(wx.EVT_BUTTON, self.on_send)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

		self.server.SetValue(normalize_server(server) or self.config.recent_servers()[0] or DEFAULT_SERVER)
		self.on_server_changed()
		if topic:
			self.topic.SetValue(topic)
		self.server.SetFocus() if not topic else self.message.SetFocus()

	# Construcción de pestañas

	def _page(self, title: str) -> tuple[wx.Panel, Form, wx.BoxSizer]:
		panel = wx.Panel(self.book)
		form = Form(panel)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(form.sizer, 1, wx.EXPAND | wx.ALL, 10)
		panel.SetSizer(sizer)
		self.book.AddPage(panel, title)
		return panel, form, sizer

	def _build_general(self):
		panel, form, _sizer = self._page(_("General"))
		self.server = form.combo(_("&Servidor:"), self.config.recent_servers())
		self.topic = form.combo(_("&Tema:"))
		self.title = form.text(_("Tít&ulo:"))
		self.message = form.multiline(_("&Mensaje:"), height=120)
		self.markdown = form.check(_("El mensaje usa formato Mar&kdown"))
		self.priority = form.choice(_("&Prioridad:"), priority_labels(), 2)
		self.tags = form.text(_("E&tiquetas (separadas por comas):"), hint="warning,skull")
		self.click = form.text(_("URL al pulsar la notificación (c&lic):"), hint="https://")
		self.icon = form.text(_("URL del &icono (JPEG o PNG):"), hint="https://")
		self.server.Bind(wx.EVT_COMBOBOX, lambda e: self.on_server_changed())
		self.server.Bind(wx.EVT_KILL_FOCUS, self._on_server_kill_focus)

	def _build_attachment(self):
		panel, form, sizer = self._page(_("Adjunto"))
		self.attach_kind = wx.RadioBox(
			panel,
			label=_("Tipo de adjunto"),
			choices=[_("Ninguno"), _("Archivo local"), _("URL externa")],
			majorDimension=1,
			style=wx.RA_SPECIFY_COLS,
		)
		sizer.Insert(0, self.attach_kind, 0, wx.EXPAND | wx.ALL, 10)
		self.file_path, self.browse_btn = form.row(
			_("&Archivo:"),
			lambda p: wx.TextCtrl(p),
			lambda p: wx.Button(p, label=_("E&xaminar…")),
		)
		self.attach_url = form.text(_("&URL del adjunto:"), hint="https://")
		self.filename = form.text(_("&Nombre del archivo mostrado:"))
		self.file_info = form.static(_("Información:"))
		self.attach_kind.Bind(wx.EVT_RADIOBOX, lambda e: self.update_attachment_state())
		self.browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
		self.file_path.Bind(wx.EVT_KILL_FOCUS, self._on_file_kill_focus)
		self.update_attachment_state()

	def _build_actions(self):
		panel = wx.Panel(self.book)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(panel, label=_("Acci&ones (máximo 3):")), 0, wx.LEFT | wx.TOP, 10)
		self.actions_list = wx.ListBox(panel, size=(-1, 140))
		self.actions_list.SetName(_("Acciones"))
		sizer.Add(self.actions_list, 1, wx.EXPAND | wx.ALL, 10)
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.add_action_btn = wx.Button(panel, label=_("&Añadir…"))
		self.edit_action_btn = wx.Button(panel, label=_("&Editar…"))
		self.remove_action_btn = wx.Button(panel, label=_("E&liminar"))
		self.up_action_btn = wx.Button(panel, label=_("S&ubir"))
		self.down_action_btn = wx.Button(panel, label=_("&Bajar"))
		for btn in (
			self.add_action_btn,
			self.edit_action_btn,
			self.remove_action_btn,
			self.up_action_btn,
			self.down_action_btn,
		):
			row.Add(btn, 0, wx.RIGHT, 6)
		sizer.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		panel.SetSizer(sizer)
		self.book.AddPage(panel, _("Acciones"))
		self.add_action_btn.Bind(wx.EVT_BUTTON, self.on_add_action)
		self.edit_action_btn.Bind(wx.EVT_BUTTON, self.on_edit_action)
		self.remove_action_btn.Bind(wx.EVT_BUTTON, self.on_remove_action)
		self.up_action_btn.Bind(wx.EVT_BUTTON, lambda e: self.move_action(-1))
		self.down_action_btn.Bind(wx.EVT_BUTTON, lambda e: self.move_action(1))
		self.actions_list.Bind(wx.EVT_LISTBOX, lambda e: self.update_action_buttons())
		self.actions_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_edit_action)
		self.update_action_buttons()

	def _build_delivery(self):
		panel, form, sizer = self._page(_("Entrega"))
		self.delay = form.text(
			_("&Programar entrega (vacío = inmediata):"), hint=_("30m, 2h, tomorrow 10am, 1735689600…")
		)
		self.timezone = form.text(_("&Zona horaria para la programación:"), hint="Europe/Madrid")
		self.email = form.text(_("Reenviar por correo &electrónico a:"))
		self.call = form.text(_("&Llamada telefónica a (número con prefijo o «yes»):"))
		self.sequence_id = form.text(_("&ID de secuencia (para actualizar una notificación):"))
		self.template = form.choice(
			_("Pla&ntilla:"),
			[_("Ninguna"), _("Plantilla en línea (yes)"), "GitHub", "Grafana", "Alertmanager"],
		)
		self.no_cache = form.check(_("No guardar en la &caché del servidor"))
		self.no_firebase = form.check(_("No reenviar a &Firebase"))
		self.unifiedpush = form.check(_("Mensaje &UnifiedPush"))
		hint = wx.StaticText(
			panel,
			label=_(
				"La entrega puede programarse entre 10 segundos y 3 días. Con el ID de secuencia se puede "
				"actualizar, marcar como leída o eliminar una notificación ya enviada."
			),
		)
		hint.Wrap(560)
		sizer.Add(hint, 0, wx.ALL, 10)
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.clear_btn = wx.Button(panel, label=_("Marcar como lei&da en los clientes"))
		self.delete_btn = wx.Button(panel, label=_("Eliminar de los clien&tes"))
		row.Add(self.clear_btn, 0, wx.RIGHT, 6)
		row.Add(self.delete_btn, 0)
		sizer.Add(row, 0, wx.ALL, 10)
		self.clear_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_sequence_operation("clear"))
		self.delete_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_sequence_operation("delete"))

	def _build_auth(self):
		panel = wx.Panel(self.book)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.auth = AuthPanel(panel)
		sizer.Add(self.auth, 0, wx.EXPAND | wx.ALL, 4)
		self.auth_status = wx.StaticText(panel, label="")
		sizer.Add(self.auth_status, 0, wx.ALL, 10)
		panel.SetSizer(sizer)
		self.book.AddPage(panel, _("Autenticación"))

	# Eventos generales

	def on_char_hook(self, event):
		# Ctrl+Intro envía desde cualquier campo (Intro sola sirve para saltos de línea en el mensaje).
		if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and event.ControlDown():
			self.on_send(None)
			return
		event.Skip()

	def _on_server_kill_focus(self, event):
		event.Skip()
		wx.CallAfter(self.on_server_changed)

	def on_server_changed(self):
		server = normalize_server(self.server.GetValue())
		if server == self._limits_server:
			return
		self._limits_server = server
		self.limits = None
		topic = self.topic.GetValue()
		self.topic.Set(self.config.recent_topics(server))
		self.topic.SetValue(topic)
		vault = self.controller.vault
		cred = vault.get(server) if server else None
		self.auth.set_credential(cred)
		if cred:
			self.auth_status.SetLabel(_("Se usan las credenciales guardadas para este servidor."))
		elif vault.exists() and not vault.unlocked:
			self.auth_status.SetLabel(_("Hay credenciales guardadas, pero el almacén está bloqueado."))
		else:
			self.auth_status.SetLabel("")
		self.update_file_info()

	# Adjuntos

	def update_attachment_state(self):
		kind = self.attach_kind.GetSelection()
		self.file_path.Enable(kind == ATTACH_FILE)
		self.browse_btn.Enable(kind == ATTACH_FILE)
		self.attach_url.Enable(kind == ATTACH_URL)
		self.filename.Enable(kind != ATTACH_NONE)
		self.update_file_info()

	def on_browse(self, event):
		with wx.FileDialog(self, _("Elegir archivo para adjuntar"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				self.file_path.SetValue(dlg.GetPath())
				if not self.filename.GetValue():
					self.filename.SetValue(os.path.basename(dlg.GetPath()))
				self.update_file_info()
				self.file_path.SetFocus()

	def _on_file_kill_focus(self, event):
		event.Skip()
		wx.CallAfter(self.update_file_info)

	def _current_auth(self):
		return publisher.auth_header(self.auth.get_credential())

	def get_limits(self):
		"""Límites de adjuntos del servidor (se consultan una vez por servidor)."""
		if self.limits is None:
			with wx.BusyCursor():
				self.limits = publisher.fetch_limits(self.server.GetValue(), self._current_auth())
		return self.limits

	def update_file_info(self):
		if not hasattr(self, "file_info"):
			return
		if self.attach_kind.GetSelection() != ATTACH_FILE:
			self.file_info.SetValue("")
			return
		path = self.file_path.GetValue().strip()
		limits = self.limits or publisher.default_limits(self.server.GetValue())
		limit_text = _("límite {size} por archivo").format(size=publisher.format_size(limits.file_size))
		if not limits.from_server:
			limit_text += " " + _("(documentado)")
		if path and os.path.isfile(path):
			size = os.path.getsize(path)
			self.file_info.SetValue(
				_("Tamaño {size}; {limit}.").format(size=publisher.format_size(size), limit=limit_text)
			)
		else:
			self.file_info.SetValue(limit_text.capitalize())

	# Acciones

	def refresh_actions(self, select: int | None = None):
		labels = type_labels()
		self.actions_list.Set([f"{a.label} — {labels.get(a.action, a.action)}" for a in self.actions])
		if select is not None and 0 <= select < len(self.actions):
			self.actions_list.SetSelection(select)
		self.update_action_buttons()

	def update_action_buttons(self):
		index = self.actions_list.GetSelection()
		has = index != wx.NOT_FOUND
		self.add_action_btn.Enable(len(self.actions) < MAX_ACTIONS)
		self.edit_action_btn.Enable(has)
		self.remove_action_btn.Enable(has)
		self.up_action_btn.Enable(has and index > 0)
		self.down_action_btn.Enable(has and index < len(self.actions) - 1)

	def on_add_action(self, event):
		if len(self.actions) >= MAX_ACTIONS:
			return
		with ActionDialog(self) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				self.actions.append(dlg.get_action())
				self.refresh_actions(len(self.actions) - 1)
		self.actions_list.SetFocus()

	def on_edit_action(self, event):
		index = self.actions_list.GetSelection()
		if index == wx.NOT_FOUND:
			return
		with ActionDialog(self, self.actions[index]) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				self.actions[index] = dlg.get_action()
				self.refresh_actions(index)
		self.actions_list.SetFocus()

	def on_remove_action(self, event):
		index = self.actions_list.GetSelection()
		if index == wx.NOT_FOUND:
			return
		del self.actions[index]
		self.refresh_actions(min(index, len(self.actions) - 1))
		self.actions_list.SetFocus()

	def move_action(self, delta: int):
		index = self.actions_list.GetSelection()
		target = index + delta
		if index == wx.NOT_FOUND or not 0 <= target < len(self.actions):
			return
		self.actions[index], self.actions[target] = self.actions[target], self.actions[index]
		self.refresh_actions(target)
		self.actions_list.SetFocus()

	# Envío

	def build_request(self) -> PublishRequest:
		kind = self.attach_kind.GetSelection()
		return PublishRequest(
			server=normalize_server(self.server.GetValue()),
			topic=self.topic.GetValue().strip(),
			message=self.message.GetValue(),
			title=self.title.GetValue().strip(),
			priority=self.priority.GetSelection() + 1,
			tags=[t.strip() for t in self.tags.GetValue().split(",") if t.strip()],
			markdown=self.markdown.GetValue(),
			click=self.click.GetValue().strip(),
			icon=self.icon.GetValue().strip(),
			attach_url=self.attach_url.GetValue().strip() if kind == ATTACH_URL else "",
			file_path=self.file_path.GetValue().strip() if kind == ATTACH_FILE else "",
			filename=self.filename.GetValue().strip() if kind != ATTACH_NONE else "",
			actions=list(self.actions),
			delay=self.delay.GetValue().strip(),
			timezone=self.timezone.GetValue().strip(),
			email=self.email.GetValue().strip(),
			call=self.call.GetValue().strip(),
			sequence_id=self.sequence_id.GetValue().strip(),
			cache=not self.no_cache.GetValue(),
			firebase=not self.no_firebase.GetValue(),
			unifiedpush=self.unifiedpush.GetValue(),
			template=TEMPLATE_VALUES[self.template.GetSelection()],
		)

	def _focus_field(self, field: str) -> wx.Window:
		mapping = {
			"server": (0, self.server),
			"topic": (0, self.topic),
			"title": (0, self.title),
			"tags": (0, self.tags),
			"priority": (0, self.priority),
			"attach": (1, self.attach_kind),
			"file": (1, self.file_path),
			"actions": (2, self.actions_list),
			"template": (3, self.template),
			"auth": (4, self.auth.kind),
		}
		page, ctrl = mapping.get(field, (0, self.message))
		self.book.SetSelection(page)
		return ctrl

	def _store_credentials(self, server: str) -> bool:
		cred = self.auth.get_credential()
		if not cred or not self.auth.wants_save:
			return True
		if self.controller.vault.get(server) == cred:
			return True
		if not ensure_vault(self, self.controller.vault):
			return False
		self.controller.vault.set(server, cred["type"], cred["username"], cred["secret"])
		self.controller.credentials_changed()
		return True

	def on_send(self, event):
		if self._sending:
			return
		req = self.build_request()
		errors = publisher.validate_request(req)
		if self.attach_kind.GetSelection() == ATTACH_FILE and not req.file_path:
			errors.append(("file", _("Elige el archivo que quieres adjuntar.")))
		if self.attach_kind.GetSelection() == ATTACH_URL and not req.attach_url:
			errors.append(("attach", _("Escribe la URL del adjunto.")))
		problem = self.auth.validate()
		if problem:
			errors.append(("auth", problem[0]))
		if errors:
			field, message = errors[0]
			ctrl = self._focus_field(field)
			error(self, message, ctrl)
			return
		if req.file_path:
			file_error = publisher.validate_file(req.file_path, self.get_limits())
			self.update_file_info()
			if file_error:
				error(self, file_error, self._focus_field("file"))
				return
		if publisher.message_too_long(req) and not req.file_path:
			if not confirm(
				self,
				_("El mensaje supera 4096 bytes y el servidor lo convertirá en un archivo adjunto. ¿Enviar igualmente?"),
			):
				self._focus_field("message").SetFocus()
				return
		if not self._store_credentials(req.server):
			return
		self._send(req)

	def _send(self, req: PublishRequest):
		auth = self._current_auth()
		cancel = threading.Event()
		progress_dialog = None
		if req.file_path:
			progress_dialog = wx.ProgressDialog(
				_("Enviando adjunto"),
				_("Subiendo {name}…").format(name=os.path.basename(req.file_path)),
				maximum=100,
				parent=self,
				style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_AUTO_HIDE | wx.PD_ELAPSED_TIME,
			)
		self._sending = True
		self.send_btn.Disable()
		state = {"last": -1}

		def progress(sent, total):
			percent = int(sent * 100 / total) if total else 100
			if percent != state["last"]:
				state["last"] = percent
				wx.CallAfter(update_progress, min(percent, 99))

		def update_progress(percent):
			if progress_dialog:
				keep_going, _skip = progress_dialog.Update(percent)
				if not keep_going:
					cancel.set()

		def worker():
			try:
				result = publisher.publish(req, auth, progress if req.file_path else None, cancel)
				wx.CallAfter(done, result, None)
			except publisher.PublishCancelled:
				wx.CallAfter(done, None, "cancelled")
			except publisher.PublishError as exc:
				wx.CallAfter(done, None, str(exc))
			except Exception as exc:  # noqa: BLE001
				wx.CallAfter(done, None, str(exc))

		def done(result, problem):
			self._sending = False
			if progress_dialog:
				progress_dialog.Destroy()
			if not self:
				return
			self.send_btn.Enable()
			if problem == "cancelled":
				info(self, _("Envío cancelado."))
				return
			if problem:
				error(self, _("No se pudo publicar la notificación.\n\n{error}").format(error=problem))
				return
			self.config.add_recent_server(req.server, save=False)
			self.config.add_recent_topic(req.server, req.topic)
			self.limits = None
			self.controller.set_status(_("Notificación publicada en {topic}.").format(topic=req.topic))
			if result is not None and result.id and not req.sequence_id:
				self.sequence_id.SetHint(result.id)
			wx.Bell()
			self.ask_after_send()

		threading.Thread(target=worker, name="ntfy-publish", daemon=True).start()

	def ask_after_send(self):
		dlg = wx.MessageDialog(
			self,
			_("La notificación se ha publicado correctamente. ¿Quieres enviar otra?"),
			_("Publicada"),
			wx.YES_NO | wx.ICON_INFORMATION,
		)
		dlg.SetYesNoLabels(_("Enviar &otra"), _("&Cerrar"))
		answer = dlg.ShowModal()
		dlg.Destroy()
		if answer == wx.ID_YES:
			self.message.SetValue("")
			self.book.SetSelection(0)
			self.message.SetFocus()
		else:
			self.EndModal(wx.ID_OK)

	def on_sequence_operation(self, operation: str):
		server = normalize_server(self.server.GetValue())
		topic = self.topic.GetValue().strip()
		seq = self.sequence_id.GetValue().strip()
		if not seq:
			error(self, _("Escribe el ID de secuencia de la notificación."), self.sequence_id)
			return
		errors = [e for e in publisher.validate_request(PublishRequest(server=server, topic=topic)) if e[0] in ("server", "topic")]
		if errors:
			error(self, errors[0][1], self._focus_field(errors[0][0]))
			return
		auth = self._current_auth()
		try:
			with wx.BusyCursor():
				if operation == "clear":
					publisher.clear_notification(server, topic, seq, auth)
				else:
					publisher.delete_notification(server, topic, seq, auth)
		except publisher.PublishError as exc:
			error(self, str(exc))
			return
		info(self, _("Operación enviada correctamente."))
