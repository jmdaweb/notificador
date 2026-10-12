"""Internacionalización basada en gettext. Las cadenas fuente están en español."""

import builtins
import gettext
import locale
import wx

from . import paths

DOMAIN = "notificador"


def install(language: str | None = None) -> None:
	languages = None
	lc = wx.Locale()
	if language:
		languages = [language]
		wxLang=lc.FindLanguageInfo(language)
	else:
		wxLang=lc.FindLanguageInfo("es_ES")
		try:
			lang = locale.getlocale()[0]
			if lang:
				languages = [lang]
		except ValueError:
			pass
	translation = gettext.translation(
		DOMAIN, localedir=str(paths.locale_dir()), languages=languages, fallback=True
	)
	translation.install()
	return lc, wxLang.Language


def _(text: str) -> str:
	"""Acceso a la función de traducción instalada (o identidad si no se ha instalado)."""
	func = getattr(builtins, "_", None)
	if func is None or func is _:
		return text
	return func(text)


def ngettext(singular: str, plural: str, n: int) -> str:
	func = getattr(builtins, "ngettext", None)
	if func is None:
		return singular if n == 1 else plural
	return func(singular, plural, n)
