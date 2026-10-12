"""Diálogos de la contraseña maestra que protege las credenciales."""

import wx

from ..i18n import _
from ..vault import Vault, VaultError, WrongPasswordError
from .a11y import Form, dialog_buttons, error

MIN_LENGTH = 6


class PasswordDialog(wx.Dialog):
	"""mode: "create" (nueva + confirmación), "unlock" (una contraseña) o "change" (actual + nueva + confirmación)."""

	def __init__(self, parent, mode: str, message: str = ""):
		titles = {
			"create": _("Crear contraseña maestra"),
			"unlock": _("Desbloquear credenciales"),
			"change": _("Cambiar contraseña maestra"),
		}
		super().__init__(parent, title=titles[mode], style=wx.DEFAULT_DIALOG_STYLE)
		self.mode = mode
		panel = wx.Panel(self)
		root = wx.BoxSizer(wx.VERTICAL)
		if message:
			text = wx.StaticText(panel, label=message)
			text.Wrap(420)
			root.Add(text, 0, wx.ALL, 10)
		form = Form(panel)
		self.current = None
		self.confirm = None
		if mode == "change":
			self.current = form.password(_("Contraseña &actual:"))
		label = _("&Contraseña maestra:") if mode != "change" else _("&Nueva contraseña:")
		self.password = form.password(label)
		if mode in ("create", "change"):
			self.confirm = form.password(_("&Repetir contraseña:"))
		root.Add(form.sizer, 0, wx.EXPAND | wx.ALL, 10)
		panel.SetSizer(root)

		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(panel, 1, wx.EXPAND)
		ok_label = _("&Desbloquear") if mode == "unlock" else _("Aceptar")
		buttons = dialog_buttons(self, ok_label)
		if mode == "unlock":
			self.FindWindowById(wx.ID_CANCEL, self).SetLabel(_("&Omitir"))
		outer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(outer)
		self.SetMinSize((440, -1))
		self.Fit()
		self.CentreOnParent()
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		(self.current or self.password).SetFocus()

	def on_ok(self, event):
		value = self.password.GetValue()
		if self.mode in ("create", "change"):
			if len(value) < MIN_LENGTH:
				error(self, _("La contraseña debe tener al menos {n} caracteres.").format(n=MIN_LENGTH), self.password)
				return
			if value != self.confirm.GetValue():
				error(self, _("Las contraseñas no coinciden."), self.confirm)
				return
		elif not value:
			error(self, _("Escribe la contraseña."), self.password)
			return
		self.EndModal(wx.ID_OK)


def unlock_vault(parent, vault: Vault, attempts: int = 3) -> bool:
	"""Pide la contraseña hasta desbloquear el almacén. Devuelve False si el usuario omite."""
	if vault.unlocked:
		return True
	message = _("Las credenciales guardadas están cifradas. Escribe la contraseña maestra para usarlas.")
	for attempt in range(attempts):
		with PasswordDialog(parent, "unlock", message) as dlg:
			if dlg.ShowModal() != wx.ID_OK:
				return False
			password = dlg.password.GetValue()
		busy = wx.BusyCursor()
		try:
			vault.unlock(password)
			return True
		except WrongPasswordError:
			message = _("Contraseña incorrecta. Intentos restantes: {n}.").format(n=attempts - attempt - 1)
		except VaultError as exc:
			error(parent, str(exc))
			return False
		finally:
			del busy
	error(parent, _("No se ha podido desbloquear el almacén de credenciales."))
	return False


def ensure_vault(parent, vault: Vault) -> bool:
	"""Garantiza que el almacén existe y está desbloqueado (creándolo si hace falta)."""
	if vault.unlocked:
		return True
	if vault.exists():
		return unlock_vault(parent, vault)
	message = _(
		"Para guardar credenciales se cifrarán con una contraseña maestra. "
		"Se pedirá al iniciar el programa. Si la olvidas, tendrás que volver a introducir las credenciales."
	)
	with PasswordDialog(parent, "create", message) as dlg:
		if dlg.ShowModal() != wx.ID_OK:
			return False
		password = dlg.password.GetValue()
	with wx.BusyCursor():
		vault.create(password)
	return True


def change_master_password(parent, vault: Vault) -> bool:
	if not vault.exists():
		return ensure_vault(parent, vault)
	with PasswordDialog(parent, "change") as dlg:
		if dlg.ShowModal() != wx.ID_OK:
			return False
		current = dlg.current.GetValue()
		new = dlg.password.GetValue()
	try:
		with wx.BusyCursor():
			check = Vault(vault.path)
			check.unlock(current)
			if not vault.unlocked:
				vault.unlock(current)
			vault.change_password(new)
	except WrongPasswordError:
		error(parent, _("La contraseña actual no es correcta."))
		return False
	except VaultError as exc:
		error(parent, str(exc))
		return False
	return True
