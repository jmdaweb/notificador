"""Gestión de servidores conocidos y de sus credenciales."""

import wx

from ..config import normalize_server
from ..i18n import _
from .a11y import Form, confirm, dialog_buttons, error
from .master_password_dialog import ensure_vault

AUTH_NONE, AUTH_BASIC, AUTH_TOKEN = 0, 1, 2


class AuthPanel(wx.Panel):
	"""Controles de autenticación reutilizables: tipo, usuario, contraseña, token y «guardar»."""

	def __init__(self, parent, show_save=True):
		super().__init__(parent)
		form = Form(self)
		self.kind = form.choice(
			_("&Autenticación:"), [_("Ninguna"), _("Usuario y contraseña"), _("Token de acceso")]
		)
		self.username = form.text(_("&Usuario:"))
		self.password = form.password(_("Con&traseña:"))
		self.token = form.password(_("To&ken:"))
		self.save = form.check(_("&Guardar credenciales para este servidor"), True) if show_save else None
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(form.sizer, 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizer(sizer)
		self.kind.Bind(wx.EVT_CHOICE, lambda e: self.update_state())
		self.update_state()

	def update_state(self):
		kind = self.kind.GetSelection()
		self.username.Enable(kind == AUTH_BASIC)
		self.password.Enable(kind == AUTH_BASIC)
		self.token.Enable(kind == AUTH_TOKEN)
		if self.save is not None:
			self.save.Enable(kind != AUTH_NONE)

	def set_credential(self, cred: dict | None) -> None:
		self.username.SetValue("")
		self.password.SetValue("")
		self.token.SetValue("")
		if not cred:
			self.kind.SetSelection(AUTH_NONE)
		elif cred.get("type") == "token":
			self.kind.SetSelection(AUTH_TOKEN)
			self.token.SetValue(cred.get("secret", ""))
		else:
			self.kind.SetSelection(AUTH_BASIC)
			self.username.SetValue(cred.get("username", ""))
			self.password.SetValue(cred.get("secret", ""))
		self.update_state()

	def get_credential(self) -> dict | None:
		kind = self.kind.GetSelection()
		if kind == AUTH_BASIC:
			return {"type": "basic", "username": self.username.GetValue(), "secret": self.password.GetValue()}
		if kind == AUTH_TOKEN:
			return {"type": "token", "username": "", "secret": self.token.GetValue().strip()}
		return None

	def validate(self) -> tuple[str, wx.Window] | None:
		kind = self.kind.GetSelection()
		if kind == AUTH_BASIC and not self.username.GetValue():
			return _("Escribe el nombre de usuario."), self.username
		if kind == AUTH_TOKEN and not self.token.GetValue().strip():
			return _("Escribe el token de acceso."), self.token
		return None

	@property
	def wants_save(self) -> bool:
		return bool(self.save and self.save.IsEnabled() and self.save.GetValue())


class CredentialEditDialog(wx.Dialog):
	def __init__(self, parent, server: str = "", cred: dict | None = None, servers=()):
		super().__init__(parent, title=_("Credenciales del servidor"))
		panel = wx.Panel(self)
		form = Form(panel)
		self.server = form.combo(_("&Servidor:"), servers, server)
		if server:
			self.server.Disable()
		root = wx.BoxSizer(wx.VERTICAL)
		root.Add(form.sizer, 0, wx.EXPAND | wx.ALL, 10)
		self.auth = AuthPanel(panel, show_save=False)
		self.auth.set_credential(cred)
		root.Add(self.auth, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
		panel.SetSizer(root)
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		outer.Add(dialog_buttons(self), 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(outer)
		self.SetMinSize((460, -1))
		self.Fit()
		self.CentreOnParent()
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		(self.auth.kind if server else self.server).SetFocus()

	def on_ok(self, event):
		if not normalize_server(self.server.GetValue()):
			error(self, _("Indica un servidor."), self.server)
			return
		problem = self.auth.validate()
		if problem:
			error(self, problem[0], problem[1])
			return
		self.EndModal(wx.ID_OK)


class CredentialsDialog(wx.Dialog):
	"""Lista de servidores (recientes, suscritos y con credenciales)."""

	def __init__(self, parent, controller):
		super().__init__(parent, title=_("Servidores y credenciales"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.controller = controller
		panel = wx.Panel(self)
		root = wx.BoxSizer(wx.VERTICAL)
		root.Add(wx.StaticText(panel, label=_("&Servidores:")), 0, wx.LEFT | wx.TOP, 10)
		self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(560, 220))
		self.list.SetName(_("Servidores"))
		self.list.InsertColumn(0, _("Servidor"), width=260)
		self.list.InsertColumn(1, _("Autenticación"), width=170)
		self.list.InsertColumn(2, _("Origen"), width=120)
		root.Add(self.list, 1, wx.EXPAND | wx.ALL, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		self.add_btn = wx.Button(panel, label=_("&Añadir credenciales…"))
		self.edit_btn = wx.Button(panel, label=_("&Editar credenciales…"))
		self.delete_btn = wx.Button(panel, label=_("&Borrar credenciales"))
		self.forget_btn = wx.Button(panel, label=_("&Quitar de recientes"))
		for btn in (self.add_btn, self.edit_btn, self.delete_btn, self.forget_btn):
			buttons.Add(btn, 0, wx.RIGHT, 6)
		root.Add(buttons, 0, wx.LEFT | wx.RIGHT, 10)
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
		self.delete_btn.Bind(wx.EVT_BUTTON, self.on_delete)
		self.forget_btn.Bind(wx.EVT_BUTTON, self.on_forget)
		self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_edit)
		self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self.update_buttons())
		close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
		self.servers: list[str] = []
		self.refresh()
		self.list.SetFocus()

	def refresh(self, select: str | None = None):
		vault = self.controller.vault
		recent = self.controller.config.recent_servers()
		subscribed = {s.server for s in self.controller.storage.subscriptions()}
		stored = set(vault.servers()) if vault.unlocked else set()
		self.servers = sorted(set(recent) | subscribed | stored)
		self.list.DeleteAllItems()
		for i, server in enumerate(self.servers):
			cred = vault.get(server)
			if cred is None:
				auth = _("Bloqueadas") if (vault.exists() and not vault.unlocked) else _("Ninguna")
			elif cred.get("type") == "token":
				auth = _("Token")
			else:
				auth = _("Usuario: {user}").format(user=cred.get("username", ""))
			origins = []
			if server in recent:
				origins.append(_("reciente"))
			if server in subscribed:
				origins.append(_("suscrito"))
			self.list.InsertItem(i, server)
			self.list.SetItem(i, 1, auth)
			self.list.SetItem(i, 2, ", ".join(origins))
		if self.servers:
			index = self.servers.index(select) if select in self.servers else 0
			self.list.Select(index)
			self.list.Focus(index)
		self.update_buttons()

	def selected(self) -> str | None:
		index = self.list.GetFirstSelected()
		return self.servers[index] if 0 <= index < len(self.servers) else None

	def update_buttons(self):
		server = self.selected()
		has = bool(server and self.controller.vault.get(server))
		self.edit_btn.Enable(bool(server))
		self.delete_btn.Enable(has)
		self.forget_btn.Enable(bool(server and server in self.controller.config.recent_servers()))

	def _edit(self, server: str):
		if not ensure_vault(self, self.controller.vault):
			return
		cred = self.controller.vault.get(server) if server else None
		with CredentialEditDialog(self, server, cred, self.servers) as dlg:
			if dlg.ShowModal() != wx.ID_OK:
				return
			server = normalize_server(dlg.server.GetValue())
			new = dlg.auth.get_credential()
		if new is None:
			self.controller.vault.delete(server)
		else:
			self.controller.vault.set(server, new["type"], new["username"], new["secret"])
		self.controller.config.add_recent_server(server)
		self.controller.credentials_changed()
		self.refresh(server)

	def on_add(self, event):
		self._edit("")

	def on_edit(self, event):
		server = self.selected()
		if server:
			self._edit(server)

	def on_delete(self, event):
		server = self.selected()
		if not server or not confirm(self, _("¿Borrar las credenciales guardadas de {server}?").format(server=server)):
			return
		if ensure_vault(self, self.controller.vault):
			self.controller.vault.delete(server)
			self.controller.credentials_changed()
			self.refresh(server)

	def on_forget(self, event):
		server = self.selected()
		if server:
			self.controller.config.remove_recent_server(server)
			self.refresh()
