"""Diálogo para definir un botón de acción de una notificación."""

import wx

from ..i18n import _
from ..ntfy.models import ACTION_TYPES, Action
from .a11y import Form, dialog_buttons, error

HTTP_METHODS = ("POST", "GET", "PUT", "DELETE", "PATCH")


def type_labels():
	return {
		"view": _("Abrir URL o aplicación (view)"),
		"http": _("Enviar petición HTTP (http)"),
		"broadcast": _("Difusión de Android (broadcast)"),
		"copy": _("Copiar al portapapeles (copy)"),
	}


def parse_pairs(text: str, separator: str) -> dict:
	"""Convierte líneas «clave<sep>valor» en un diccionario."""
	result = {}
	for line in text.splitlines():
		if separator in line:
			key, value = line.split(separator, 1)
			if key.strip():
				result[key.strip()] = value.strip()
	return result


def format_pairs(pairs: dict, separator: str) -> str:
	return "\n".join(f"{k}{separator} {v}" if separator == ":" else f"{k}{separator}{v}" for k, v in pairs.items())


class ActionDialog(wx.Dialog):
	def __init__(self, parent, action: Action | None = None):
		super().__init__(parent, title=_("Acción"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		panel = wx.Panel(self)
		form = Form(panel)
		labels = type_labels()
		self.kind = form.choice(_("&Tipo:"), [labels[t] for t in ACTION_TYPES])
		self.label = form.text(_("&Etiqueta del botón:"))
		self.url = form.text(_("&URL:"), hint="https://")
		self.clear = form.check(_("&Descartar la notificación al pulsar"))
		self.method = form.choice(_("&Método HTTP:"), HTTP_METHODS)
		self.headers = form.multiline(_("&Cabeceras (una por línea, Clave: valor):"), height=60)
		self.body = form.multiline(_("Cuer&po de la petición:"), height=60)
		self.intent = form.text(_("&Intent de Android:"), hint="io.heckel.ntfy.USER_ACTION")
		self.extras = form.multiline(_("E&xtras (una por línea, clave=valor):"), height=60)
		self.value = form.text(_("&Valor a copiar:"))
		root = wx.BoxSizer(wx.VERTICAL)
		root.Add(form.sizer, 1, wx.EXPAND | wx.ALL, 10)
		panel.SetSizer(root)
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		outer.Add(dialog_buttons(self), 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(outer)
		self.SetMinSize((520, -1))
		self.Fit()
		self.CentreOnParent()

		if action:
			self.kind.SetSelection(ACTION_TYPES.index(action.action) if action.action in ACTION_TYPES else 0)
			self.label.SetValue(action.label)
			self.url.SetValue(action.url)
			self.clear.SetValue(action.clear)
			method = (action.method or "POST").upper()
			self.method.SetSelection(HTTP_METHODS.index(method) if method in HTTP_METHODS else 0)
			self.headers.SetValue(format_pairs(action.headers, ":"))
			self.body.SetValue(action.body)
			self.intent.SetValue(action.intent)
			self.extras.SetValue(format_pairs(action.extras, "="))
			self.value.SetValue(action.value)
		self.kind.Bind(wx.EVT_CHOICE, lambda e: self.update_state())
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		self.update_state()
		self.kind.SetFocus()

	@property
	def action_type(self) -> str:
		return ACTION_TYPES[self.kind.GetSelection()]

	def update_state(self):
		kind = self.action_type
		self.url.Enable(kind in ("view", "http"))
		self.method.Enable(kind == "http")
		self.headers.Enable(kind == "http")
		self.body.Enable(kind == "http")
		self.intent.Enable(kind == "broadcast")
		self.extras.Enable(kind == "broadcast")
		self.value.Enable(kind == "copy")

	def get_action(self) -> Action:
		kind = self.action_type
		return Action(
			action=kind,
			label=self.label.GetValue().strip(),
			url=self.url.GetValue().strip() if kind in ("view", "http") else "",
			clear=self.clear.GetValue(),
			method=HTTP_METHODS[self.method.GetSelection()] if kind == "http" else "",
			headers=parse_pairs(self.headers.GetValue(), ":") if kind == "http" else {},
			body=self.body.GetValue() if kind == "http" else "",
			intent=self.intent.GetValue().strip() if kind == "broadcast" else "",
			extras=parse_pairs(self.extras.GetValue(), "=") if kind == "broadcast" else {},
			value=self.value.GetValue() if kind == "copy" else "",
		)

	def on_ok(self, event):
		action = self.get_action()
		problem = action.validate()
		if problem == "missing_label":
			error(self, _("Escribe la etiqueta del botón."), self.label)
		elif problem == "missing_url":
			error(self, _("Esta acción necesita una URL."), self.url)
		elif problem == "missing_value":
			error(self, _("Escribe el valor que se copiará."), self.value)
		else:
			self.EndModal(wx.ID_OK)
