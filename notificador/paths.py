"""Rutas de la aplicación. Todo se guarda junto al programa para que sea portable."""

import os
import sys
from pathlib import Path


def base_dir() -> Path:
	"""Carpeta donde reside el programa (el .exe si está empaquetado, o la raíz del código)."""
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent
	return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
	"""Carpeta de recursos de solo lectura (locale, etc.)."""
	if getattr(sys, "frozen", False):
		return Path(getattr(sys, "_MEIPASS", base_dir()))
	return base_dir()


_data_override = None


def set_data_dir(path) -> None:
	"""Permite cambiar la carpeta de datos (usado por las pruebas)."""
	global _data_override
	_data_override = Path(path) if path else None


def data_dir() -> Path:
	path = _data_override or (base_dir() / "data")
	path.mkdir(parents=True, exist_ok=True)
	return path


def attachments_dir() -> Path:
	path = data_dir() / "attachments"
	path.mkdir(parents=True, exist_ok=True)
	return path


def icons_dir() -> Path:
	path = data_dir() / "icons"
	path.mkdir(parents=True, exist_ok=True)
	return path


def config_file() -> Path:
	return data_dir() / "config.json"


def vault_file() -> Path:
	return data_dir() / "credentials.vault"


def database_file() -> Path:
	return data_dir() / "notificador.db"


def log_file() -> Path:
	return data_dir() / "notificador.log"


def app_icon_file() -> Path:
	return data_dir() / "notificador.ico"


def locale_dir() -> Path:
	return resource_dir() / "locale"


def is_data_dir_writable() -> bool:
	try:
		probe = data_dir() / ".write_test"
		probe.write_text("ok", encoding="utf-8")
		probe.unlink()
		return True
	except OSError:
		return False


def atomic_write_bytes(path: Path, data: bytes) -> None:
	"""Escribe un archivo de forma atómica (archivo temporal + reemplazo)."""
	tmp = path.with_name(path.name + ".tmp")
	with open(tmp, "wb") as fh:
		fh.write(data)
		fh.flush()
		os.fsync(fh.fileno())
	os.replace(tmp, path)
