"""Utilidades para construir formularios accesibles con lectores de pantalla.

Regla general: la etiqueta (wx.StaticText) se crea siempre justo antes del control al que
describe, de modo que Windows/NVDA la asocian como su nombre; además se asigna SetName().
"""

import wx


def strip_mnemonic(label: str) -> str:
	text = label.replace("&&", "\0").replace("&", "").replace("\0", "&")
	return text.rstrip(":").strip()


class Form:
	"""Rejilla de dos columnas (etiqueta, control) para un panel."""

	def __init__(self, parent: wx.Window, growable=True):
		self.parent = parent
		self.sizer = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
		if growable:
			self.sizer.AddGrowableCol(1, 1)

	def _add(self, label: str, ctrl: wx.Window, proportion_row=False) -> wx.Window:
		ctrl.SetName(strip_mnemonic(label))
		self.sizer.Add(ctrl, 1, wx.EXPAND)
		if proportion_row:
			self.sizer.AddGrowableRow(self.sizer.GetEffectiveRowsCount() - 1, 1)
		return ctrl

	def _label(self, label: str) -> wx.StaticText:
		st = wx.StaticText(self.parent, label=label)
		self.sizer.Add(st, 0, wx.ALIGN_CENTER_VERTICAL)
		return st

	def text(self, label: str, value: str = "", style: int = 0, hint: str = "") -> wx.TextCtrl:
		self._label(label)
		ctrl = wx.TextCtrl(self.parent, value=value, style=style)
		if hint:
			ctrl.SetHint(hint)
		return self._add(label, ctrl)

	def password(self, label: str, value: str = "") -> wx.TextCtrl:
		return self.text(label, value, style=wx.TE_PASSWORD)

	def multiline(self, label: str, value: str = "", height: int = 90, readonly=False) -> wx.TextCtrl:
		self._label(label)
		style = wx.TE_MULTILINE | (wx.TE_READONLY if readonly else 0)
		ctrl = wx.TextCtrl(self.parent, value=value, style=style, size=(-1, height))
		return self._add(label, ctrl, proportion_row=True)

	def combo(self, label: str, choices=(), value: str = "") -> wx.ComboBox:
		self._label(label)
		ctrl = wx.ComboBox(self.parent, value=value, choices=list(choices), style=wx.CB_DROPDOWN)
		return self._add(label, ctrl)

	def choice(self, label: str, choices=(), selection: int = 0) -> wx.Choice:
		self._label(label)
		ctrl = wx.Choice(self.parent, choices=list(choices))
		if choices:
			ctrl.SetSelection(selection)
		return self._add(label, ctrl)

	def spin(self, label: str, value: int = 0, min_value: int = 0, max_value: int = 100) -> wx.SpinCtrl:
		self._label(label)
		ctrl = wx.SpinCtrl(self.parent, min=min_value, max=max_value, initial=value)
		return self._add(label, ctrl)

	def check(self, label: str, value: bool = False) -> wx.CheckBox:
		self.sizer.AddSpacer(0)
		ctrl = wx.CheckBox(self.parent, label=label)
		ctrl.SetValue(value)
		ctrl.SetName(strip_mnemonic(label))
		self.sizer.Add(ctrl, 0)
		return ctrl

	def static(self, label: str, value: str = "") -> wx.TextCtrl:
		"""Información de solo lectura que el lector de pantalla puede recorrer."""
		self._label(label)
		ctrl = wx.TextCtrl(self.parent, value=value, style=wx.TE_READONLY | wx.BORDER_NONE)
		ctrl.SetBackgroundColour(self.parent.GetBackgroundColour())
		return self._add(label, ctrl)

	def row(self, label: str, *ctrls_factories) -> list[wx.Window]:
		"""Etiqueta seguida de varios controles en la misma fila (p. ej. ruta + «Examinar…»)."""
		self._label(label)
		box = wx.BoxSizer(wx.HORIZONTAL)
		ctrls = []
		for i, factory in enumerate(ctrls_factories):
			ctrl = factory(self.parent)
			if i == 0:
				ctrl.SetName(strip_mnemonic(label))
			box.Add(ctrl, 1 if i == 0 else 0, wx.EXPAND | (wx.LEFT if i else 0), 6)
			ctrls.append(ctrl)
		self.sizer.Add(box, 1, wx.EXPAND)
		return ctrls

	def spacer(self) -> None:
		self.sizer.AddSpacer(0)
		self.sizer.AddSpacer(0)


def button_row(parent: wx.Window, *buttons: tuple[int, str]) -> tuple[wx.BoxSizer, list[wx.Button]]:
	sizer = wx.BoxSizer(wx.HORIZONTAL)
	created = []
	for button_id, label in buttons:
		btn = wx.Button(parent, button_id, label)
		sizer.Add(btn, 0, wx.RIGHT, 6)
		created.append(btn)
	return sizer, created


def error(parent: wx.Window, message: str, focus: wx.Window | None = None, title: str | None = None) -> None:
	from ..i18n import _

	wx.MessageBox(message, title or _("Error"), wx.OK | wx.ICON_ERROR, parent)
	if focus is not None:
		focus.SetFocus()


def info(parent: wx.Window, message: str, title: str | None = None) -> None:
	from ..i18n import _

	wx.MessageBox(message, title or _("Información"), wx.OK | wx.ICON_INFORMATION, parent)


def confirm(parent: wx.Window, message: str, title: str | None = None) -> bool:
	from ..i18n import _

	return wx.MessageBox(message, title or _("Confirmar"), wx.YES_NO | wx.ICON_QUESTION, parent) == wx.YES


def dialog_buttons(dialog: wx.Dialog, ok_label: str | None = None) -> wx.Sizer:
	"""Botones Aceptar/Cancelar estándar con el botón por defecto y Escape configurados."""
	from ..i18n import _

	sizer = wx.StdDialogButtonSizer()
	ok = wx.Button(dialog, wx.ID_OK, ok_label or _("Aceptar"))
	ok.SetDefault()
	cancel = wx.Button(dialog, wx.ID_CANCEL, _("Cancelar"))
	sizer.AddButton(ok)
	sizer.AddButton(cancel)
	sizer.Realize()
	dialog.SetAffirmativeId(wx.ID_OK)
	dialog.SetEscapeId(wx.ID_CANCEL)
	return sizer
