"""Almacén de credenciales cifrado con una contraseña maestra.

Formato de data/credentials.vault (JSON):
	{"version": 1, "kdf": "scrypt", "n": ..., "r": ..., "p": ..., "salt": b64, "nonce": b64, "data": b64}
El contenido descifrado es un JSON {servidor: {"type": "basic"|"token", "username": ..., "secret": ...}}.
"""

import base64
import json
import os
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import paths
from .config import normalize_server

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
AAD = b"notificador-vault-v1"
# Las claves que empiezan así guardan secretos que no son de un servidor.
SECRET_PREFIX = "__"


class VaultError(Exception):
	pass


class WrongPasswordError(VaultError):
	pass


class VaultLockedError(VaultError):
	pass


def _derive_key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
	return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


class Vault:
	def __init__(self, path=None):
		self.path = path or paths.vault_file()
		self._lock = threading.RLock()
		self._key = None
		self._salt = None
		self._params = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
		self._entries = None

	def exists(self) -> bool:
		return self.path.exists()

	@property
	def unlocked(self) -> bool:
		return self._entries is not None

	def create(self, password: str) -> None:
		"""Crea un almacén vacío protegido con la contraseña indicada."""
		if not password:
			raise VaultError("La contraseña maestra no puede estar vacía")
		with self._lock:
			self._salt = os.urandom(16)
			self._params = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
			self._key = _derive_key(password, self._salt, *self._params)
			self._entries = {}
			self._save()

	def unlock(self, password: str) -> None:
		with self._lock:
			try:
				doc = json.loads(self.path.read_text(encoding="utf-8"))
				salt = base64.b64decode(doc["salt"])
				nonce = base64.b64decode(doc["nonce"])
				ciphertext = base64.b64decode(doc["data"])
				params = (int(doc["n"]), int(doc["r"]), int(doc["p"]))
			except (OSError, ValueError, KeyError, TypeError) as exc:
				raise VaultError(f"El archivo de credenciales está dañado: {exc}") from exc
			key = _derive_key(password, salt, *params)
			try:
				plain = AESGCM(key).decrypt(nonce, ciphertext, AAD)
			except InvalidTag as exc:
				raise WrongPasswordError("Contraseña maestra incorrecta") from exc
			self._salt, self._params, self._key = salt, params, key
			self._entries = json.loads(plain.decode("utf-8"))

	def lock(self) -> None:
		with self._lock:
			self._key = None
			self._entries = None

	def change_password(self, new_password: str) -> None:
		if not new_password:
			raise VaultError("La contraseña maestra no puede estar vacía")
		with self._lock:
			self._require_unlocked()
			self._salt = os.urandom(16)
			self._params = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
			self._key = _derive_key(new_password, self._salt, *self._params)
			self._save()

	def _require_unlocked(self) -> None:
		if self._entries is None:
			raise VaultLockedError("El almacén de credenciales está bloqueado")

	def _save(self) -> None:
		nonce = os.urandom(12)
		plain = json.dumps(self._entries, ensure_ascii=False).encode("utf-8")
		ciphertext = AESGCM(self._key).encrypt(nonce, plain, AAD)
		n, r, p = self._params
		doc = {
			"version": 1,
			"kdf": "scrypt",
			"n": n,
			"r": r,
			"p": p,
			"salt": base64.b64encode(self._salt).decode("ascii"),
			"nonce": base64.b64encode(nonce).decode("ascii"),
			"data": base64.b64encode(ciphertext).decode("ascii"),
		}
		paths.atomic_write_bytes(self.path, json.dumps(doc, indent="\t").encode("utf-8"))

	# Entradas

	def servers(self) -> list[str]:
		with self._lock:
			self._require_unlocked()
			return sorted(k for k in self._entries if not k.startswith(SECRET_PREFIX))

	def get(self, server: str):
		"""Devuelve la credencial del servidor o None. No falla si está bloqueado."""
		with self._lock:
			if self._entries is None:
				return None
			entry = self._entries.get(normalize_server(server))
			return dict(entry) if entry else None

	def set(self, server: str, cred_type: str, username: str = "", secret: str = "") -> None:
		if cred_type not in ("basic", "token"):
			raise ValueError(cred_type)
		with self._lock:
			self._require_unlocked()
			self._entries[normalize_server(server)] = {
				"type": cred_type,
				"username": username,
				"secret": secret,
			}
			self._save()

	def delete(self, server: str) -> None:
		with self._lock:
			self._require_unlocked()
			if self._entries.pop(normalize_server(server), None) is not None:
				self._save()

	# Secretos que no pertenecen a un servidor (p. ej. la contraseña del proxy)

	def get_secret(self, name: str) -> str:
		"""Devuelve el secreto o "" si no existe o el almacén está bloqueado."""
		with self._lock:
			if self._entries is None:
				return ""
			return str((self._entries.get(SECRET_PREFIX + name) or {}).get("secret", ""))

	def set_secret(self, name: str, secret: str) -> None:
		with self._lock:
			self._require_unlocked()
			self._entries[SECRET_PREFIX + name] = {"type": "secret", "username": "", "secret": secret}
			self._save()

	def delete_secret(self, name: str) -> None:
		with self._lock:
			self._require_unlocked()
			if self._entries.pop(SECRET_PREFIX + name, None) is not None:
				self._save()
