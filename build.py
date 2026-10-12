"""Genera una versión portable en dist/Notificador con PyInstaller.

Uso: python build.py
El resultado es una carpeta con Notificador.exe; al ejecutarlo crea «data» a su lado.
"""

import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent


def main() -> int:
	args = [
		str(ROOT / "notificador.pyw"),
		"--name=Notificador",
		"--onedir",
		"--windowed",
		"--noconfirm",
		"--clean",
		f"--distpath={ROOT / 'dist'}",
		f"--workpath={ROOT / 'build'}",
		f"--specpath={ROOT / 'build'}",
		"--collect-submodules=winrt",
		"--collect-submodules=windows_toasts",
		"--collect-data=emoji",
		"--collect-all=wx",
		"--hidden-import=websockets.asyncio.client",
		# Soporte SOCKS: PySocks para requests y python-socks para websockets.
		"--hidden-import=socks",
		"--hidden-import=urllib3.contrib.socks",
		"--collect-submodules=python_socks",
		"--optimize=2",
	]
	locale = ROOT / "locale"
	if any(locale.rglob("*.mo")):
		args.append(f"--add-data={locale};locale")
	PyInstaller.__main__.run(args)
	dist = ROOT / "dist" / "Notificador"
	for name in ("README.md", "LICENSE"):
		if (ROOT / name).exists():
			shutil.copy2(ROOT / name, dist / name)
	print(f"\nVersión portable generada en {dist}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
