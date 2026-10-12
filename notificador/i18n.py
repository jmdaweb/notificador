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


def _languages(language: str | None) -> list[str] | None:
	wanted = language or system_language()
	return [wanted] if wanted else None


def resolve(language: str | None = None) -> str:
	"""Idioma efectivo para la preferencia indicada (None o "" = idioma del sistema)."""
	found = gettext.find(DOMAIN, localedir=str(paths.locale_dir()), languages=_languages(language))
	if found:
		# .../locale/<idioma>/LC_MESSAGES/notificador.mo
		return Path(found).parent.parent.name
	return SOURCE_LANGUAGE


def install(language: str | None = None) -> str:
	"""Instala _() y ngettext() y devuelve el idioma efectivo (p. ej. «es_ES»)."""
	global current_language
	translation = gettext.translation(
		DOMAIN, localedir=str(paths.locale_dir()), languages=_languages(language), fallback=True
	)
	translation.install(names=("ngettext",))
	current_language = resolve(language)
	return current_language


def available_languages() -> list[str]:
	"""Idiomas en los que se puede mostrar el programa: el original más los catálogos instalados."""
	codes = {SOURCE_LANGUAGE}
	root = paths.locale_dir()
	if root.is_dir():
		for entry in root.iterdir():
			if (entry / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file():
				codes.add(entry.name)
	# Un catálogo «es» sería el mismo idioma que las cadenas originales.
	codes.discard(SOURCE_LANGUAGE.split("_")[0])
	return sorted(codes)


def language_name(code: str) -> str:
	"""Nombre del idioma en su propia lengua (p. ej. «English», «français»). Requiere la wx.App."""
	if code == SOURCE_LANGUAGE:
		return "Español"
	import wx

	info = wx.Locale.FindLanguageInfo(code)
	if info is None:
		return code
	name = info.DescriptionNative or info.Description or code
	return name[:1].upper() + name[1:]


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
