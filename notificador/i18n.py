"""Internacionalización basada en gettext. Las cadenas fuente están en español.

Flujo de arranque:
1. install() antes de crear la aplicación: instala _() y decide el idioma efectivo.
2. create_wx_locale() al principio de wx.App.OnInit: carga las traducciones de wxPython
   en ese mismo idioma, para que las cadenas propias y las de wx coincidan.
"""

import builtins
import gettext
import locale
import sys
from pathlib import Path

from . import paths

DOMAIN = "notificador"
SOURCE_LANGUAGE = "es_ES"

# Idioma efectivo tras install(): el del catálogo encontrado o el de las cadenas fuente.
current_language = SOURCE_LANGUAGE


def system_language() -> str | None:
	"""Idioma de la interfaz de Windows (p. ej. «es_ES»), o None si no se puede averiguar."""
	if sys.platform == "win32":
		try:
			import ctypes

			buffer = ctypes.create_unicode_buffer(85)
			if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
				return buffer.value.replace("-", "_")
		except (AttributeError, OSError):
			pass
	try:
		lang = locale.getlocale()[0]
	except ValueError:
		return None
	return lang or None


def install(language: str | None = None) -> str:
	"""Instala _() y ngettext() y devuelve el idioma efectivo (p. ej. «es_ES»)."""
	global current_language
	wanted = language or system_language()
	languages = [wanted] if wanted else None
	localedir = str(paths.locale_dir())
	translation = gettext.translation(DOMAIN, localedir=localedir, languages=languages, fallback=True)
	translation.install(names=("ngettext",))
	found = gettext.find(DOMAIN, localedir=localedir, languages=languages)
	if found:
		# .../locale/<idioma>/LC_MESSAGES/notificador.mo
		current_language = Path(found).parent.parent.name
	else:
		current_language = SOURCE_LANGUAGE
	return current_language


def create_wx_locale(language: str | None = None):
	"""Crea el wx.Locale del idioma efectivo. Debe llamarse con la wx.App ya creada."""
	import wx

	info = wx.Locale.FindLanguageInfo(language or current_language) or wx.Locale.FindLanguageInfo(SOURCE_LANGUAGE)
	return wx.Locale(info.Language)


def _(text: str) -> str:
	"""Acceso a la función de traducción instalada (o identidad si no se ha instalado)."""
	func = getattr(builtins, "_", None)
	if func is None or func is _:
		return text
	return func(text)


def ngettext(singular: str, plural: str, n: int) -> str:
	func = getattr(builtins, "ngettext", None)
	if func is None or func is ngettext:
		return singular if n == 1 else plural
	return func(singular, plural, n)
