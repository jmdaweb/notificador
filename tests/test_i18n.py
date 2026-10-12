import builtins
import struct

import pytest

from notificador import i18n, paths


def write_mo(path, language):
	"""Escribe un catálogo .mo mínimo (solo cabecera) para el idioma indicado."""
	header = f"Content-Type: text/plain; charset=UTF-8\nLanguage: {language}\n".encode()
	keys, values = b"\0", header + b"\0"
	start = 7 * 4 + 16
	data = struct.pack("Iiiiiii", 0x950412DE, 0, 1, 28, 36, 0, 0)
	data += struct.pack("ii", 0, start) + struct.pack("ii", len(header), start + len(keys))
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_bytes(data + keys + values)


@pytest.fixture
def locale_dir(tmp_path, monkeypatch):
	root = tmp_path / "locale"
	for code in ("en_US", "fr", "es"):
		write_mo(root / code / "LC_MESSAGES" / "notificador.mo", code)
	(root / "de" / "LC_MESSAGES").mkdir(parents=True)  # carpeta sin catálogo
	monkeypatch.setattr(paths, "locale_dir", lambda: root)
	monkeypatch.setattr(i18n, "system_language", lambda: "fr_FR")
	saved = {name: getattr(builtins, name, None) for name in ("_", "ngettext")}
	yield root
	for name, value in saved.items():
		if value is None:
			builtins.__dict__.pop(name, None)
		else:
			setattr(builtins, name, value)


def test_available_languages(locale_dir):
	assert i18n.available_languages() == ["en_US", "es_ES", "fr"]


def test_resolve(locale_dir):
	assert i18n.resolve("en_US") == "en_US"
	assert i18n.resolve(None) == "fr"  # idioma del sistema (fr_FR → catálogo fr)
	assert i18n.resolve("") == "fr"
	assert i18n.resolve("xx_YY") == "es_ES"


def test_install_sets_current_language(locale_dir):
	assert i18n.install("en_US") == "en_US" == i18n.current_language
	assert i18n.install("pt_BR") == "es_ES"


def test_available_languages_without_locale_dir(tmp_path, monkeypatch):
	monkeypatch.setattr(paths, "locale_dir", lambda: tmp_path / "no_existe")
	assert i18n.available_languages() == ["es_ES"]
