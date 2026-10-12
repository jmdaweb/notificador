"""Persistencia en SQLite (data/notificador.db): suscripciones y notificaciones recibidas."""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass

from . import paths
from .config import normalize_server
from .ntfy.models import Message

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	server TEXT NOT NULL,
	topic TEXT NOT NULL,
	alias TEXT NOT NULL DEFAULT '',
	enabled INTEGER NOT NULL DEFAULT 1,
	toasts INTEGER NOT NULL DEFAULT 1,
	since_mode TEXT NOT NULL DEFAULT 'none',
	last_id TEXT NOT NULL DEFAULT '',
	last_time INTEGER NOT NULL DEFAULT 0,
	created INTEGER NOT NULL DEFAULT 0,
	UNIQUE(server, topic)
);
CREATE TABLE IF NOT EXISTS messages (
	rowid INTEGER PRIMARY KEY AUTOINCREMENT,
	server TEXT NOT NULL,
	topic TEXT NOT NULL,
	msg_id TEXT NOT NULL,
	time INTEGER NOT NULL DEFAULT 0,
	expires INTEGER NOT NULL DEFAULT 0,
	priority INTEGER NOT NULL DEFAULT 3,
	title TEXT NOT NULL DEFAULT '',
	message TEXT NOT NULL DEFAULT '',
	tags_json TEXT NOT NULL DEFAULT '[]',
	click TEXT NOT NULL DEFAULT '',
	icon TEXT NOT NULL DEFAULT '',
	actions_json TEXT NOT NULL DEFAULT '[]',
	attachment_json TEXT NOT NULL DEFAULT '',
	sequence_id TEXT NOT NULL DEFAULT '',
	content_type TEXT NOT NULL DEFAULT '',
	raw_json TEXT NOT NULL DEFAULT '{}',
	read INTEGER NOT NULL DEFAULT 0,
	deleted INTEGER NOT NULL DEFAULT 0,
	received_at INTEGER NOT NULL DEFAULT 0,
	local_attachment TEXT NOT NULL DEFAULT '',
	UNIQUE(server, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_topic_time ON messages(server, topic, time);
CREATE INDEX IF NOT EXISTS idx_messages_seq ON messages(server, topic, sequence_id);
"""

SINCE_MODES = ("none", "12h", "all")


@dataclass
class Subscription:
	id: int
	server: str
	topic: str
	alias: str = ""
	enabled: bool = True
	toasts: bool = True
	since_mode: str = "none"
	last_id: str = ""
	last_time: int = 0

	@property
	def display_name(self) -> str:
		if self.alias:
			return self.alias
		host = self.server.split("://", 1)[-1]
		return f"{self.topic} ({host})"

	@property
	def key(self) -> tuple[str, str]:
		return (self.server, self.topic)


@dataclass
class StoredMessage:
	rowid: int
	msg: Message
	read: bool
	local_attachment: str = ""


class Storage:
	def __init__(self, path=None):
		self.path = path or paths.database_file()
		self._lock = threading.RLock()
		self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
		self._conn.row_factory = sqlite3.Row
		with self._lock:
			self._conn.execute("PRAGMA journal_mode=WAL")
			self._conn.execute("PRAGMA foreign_keys=ON")
			self._conn.executescript(SCHEMA)
			self._conn.commit()

	def close(self) -> None:
		with self._lock:
			self._conn.close()

	# Suscripciones

	@staticmethod
	def _row_to_sub(row) -> Subscription:
		return Subscription(
			id=row["id"],
			server=row["server"],
			topic=row["topic"],
			alias=row["alias"],
			enabled=bool(row["enabled"]),
			toasts=bool(row["toasts"]),
			since_mode=row["since_mode"],
			last_id=row["last_id"],
			last_time=row["last_time"],
		)

	def subscriptions(self) -> list[Subscription]:
		with self._lock:
			rows = self._conn.execute("SELECT * FROM subscriptions ORDER BY server, topic").fetchall()
		return [self._row_to_sub(r) for r in rows]

	def get_subscription(self, sub_id: int) -> Subscription | None:
		with self._lock:
			row = self._conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
		return self._row_to_sub(row) if row else None

	def find_subscription(self, server: str, topic: str) -> Subscription | None:
		with self._lock:
			row = self._conn.execute(
				"SELECT * FROM subscriptions WHERE server=? AND topic=?", (normalize_server(server), topic)
			).fetchone()
		return self._row_to_sub(row) if row else None

	def add_subscription(self, server, topic, alias="", enabled=True, toasts=True, since_mode="none") -> int:
		with self._lock:
			cur = self._conn.execute(
				"INSERT INTO subscriptions(server, topic, alias, enabled, toasts, since_mode, created)"
				" VALUES (?, ?, ?, ?, ?, ?, ?)",
				(normalize_server(server), topic, alias, int(enabled), int(toasts), since_mode, int(time.time())),
			)
			self._conn.commit()
			return cur.lastrowid

	def update_subscription(self, sub: Subscription) -> None:
		with self._lock:
			self._conn.execute(
				"UPDATE subscriptions SET server=?, topic=?, alias=?, enabled=?, toasts=?, since_mode=? WHERE id=?",
				(
					normalize_server(sub.server),
					sub.topic,
					sub.alias,
					int(sub.enabled),
					int(sub.toasts),
					sub.since_mode,
					sub.id,
				),
			)
			self._conn.commit()

	def delete_subscription(self, sub_id: int, delete_messages: bool = False) -> None:
		with self._lock:
			sub = self.get_subscription(sub_id)
			self._conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
			if sub and delete_messages:
				self._conn.execute("DELETE FROM messages WHERE server=? AND topic=?", (sub.server, sub.topic))
			self._conn.commit()

	def set_last_seen(self, server: str, topic: str, msg_id: str, msg_time: int) -> None:
		with self._lock:
			self._conn.execute(
				"UPDATE subscriptions SET last_id=?, last_time=? WHERE server=? AND topic=? AND last_time<=?",
				(msg_id, msg_time, normalize_server(server), topic, msg_time),
			)
			self._conn.commit()

	# Mensajes

	def add_message(self, msg: Message) -> tuple[int | None, list[int]]:
		"""Guarda un mensaje. Devuelve (rowid, filas sustituidas) o (None, []) si ya existía."""
		server = normalize_server(msg.server)
		with self._lock:
			exists = self._conn.execute(
				"SELECT 1 FROM messages WHERE server=? AND msg_id=?", (server, msg.id)
			).fetchone()
			if exists:
				return None, []
			replaced = []
			if msg.sequence_id:
				rows = self._conn.execute(
					"SELECT rowid FROM messages WHERE server=? AND topic=? AND deleted=0"
					" AND (sequence_id=? OR (sequence_id='' AND msg_id=?))",
					(server, msg.topic, msg.sequence_id, msg.sequence_id),
				).fetchall()
				replaced = [r["rowid"] for r in rows]
				if replaced:
					self._conn.executemany("UPDATE messages SET deleted=1 WHERE rowid=?", [(r,) for r in replaced])
			attachment = ""
			if msg.attachment:
				attachment = json.dumps(msg.raw.get("attachment") or {}, ensure_ascii=False)
			cur = self._conn.execute(
				"INSERT INTO messages(server, topic, msg_id, time, expires, priority, title, message, tags_json,"
				" click, icon, actions_json, attachment_json, sequence_id, content_type, raw_json, received_at)"
				" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
				(
					server,
					msg.topic,
					msg.id,
					msg.time,
					msg.expires,
					msg.priority,
					msg.title,
					msg.message,
					json.dumps(msg.tags, ensure_ascii=False),
					msg.click,
					msg.icon,
					json.dumps(msg.raw.get("actions") or [], ensure_ascii=False),
					attachment,
					msg.sequence_id,
					msg.content_type,
					json.dumps(msg.raw, ensure_ascii=False),
					int(time.time()),
				),
			)
			self._conn.commit()
			return cur.lastrowid, replaced

	def _rows_for_sequence(self, server, topic, sequence_id):
		return self._conn.execute(
			"SELECT rowid FROM messages WHERE server=? AND topic=? AND deleted=0"
			" AND (sequence_id=? OR (sequence_id='' AND msg_id=?))",
			(normalize_server(server), topic, sequence_id, sequence_id),
		).fetchall()

	def apply_delete(self, server: str, topic: str, sequence_id: str) -> list[int]:
		"""Evento message_delete: oculta las notificaciones de esa secuencia."""
		with self._lock:
			rowids = [r["rowid"] for r in self._rows_for_sequence(server, topic, sequence_id)]
			self._conn.executemany("UPDATE messages SET deleted=1 WHERE rowid=?", [(r,) for r in rowids])
			self._conn.commit()
		return rowids

	def apply_clear(self, server: str, topic: str, sequence_id: str) -> list[int]:
		"""Evento message_clear: marca como leídas las notificaciones de esa secuencia."""
		with self._lock:
			rowids = [r["rowid"] for r in self._rows_for_sequence(server, topic, sequence_id)]
			self._conn.executemany("UPDATE messages SET read=1 WHERE rowid=?", [(r,) for r in rowids])
			self._conn.commit()
		return rowids

	@staticmethod
	def _row_to_stored(row) -> StoredMessage:
		try:
			raw = json.loads(row["raw_json"])
		except ValueError:
			raw = {}
		raw.setdefault("id", row["msg_id"])
		raw.setdefault("topic", row["topic"])
		raw.setdefault("time", row["time"])
		msg = Message.from_dict(raw, row["server"])
		return StoredMessage(row["rowid"], msg, bool(row["read"]), row["local_attachment"])

	def query_messages(
		self, server: str | None = None, topic: str | None = None, unread_only=False, text: str = ""
	) -> list[StoredMessage]:
		sql = "SELECT * FROM messages WHERE deleted=0"
		args: list = []
		if server is not None:
			sql += " AND server=?"
			args.append(normalize_server(server))
		if topic is not None:
			sql += " AND topic=?"
			args.append(topic)
		if unread_only:
			sql += " AND read=0"
		if text:
			sql += " AND (title LIKE ? OR message LIKE ? OR tags_json LIKE ?)"
			like = f"%{text}%"
			args += [like, like, like]
		sql += " ORDER BY time DESC, rowid DESC"
		with self._lock:
			rows = self._conn.execute(sql, args).fetchall()
		return [self._row_to_stored(r) for r in rows]

	def get_message(self, rowid: int) -> StoredMessage | None:
		with self._lock:
			row = self._conn.execute("SELECT * FROM messages WHERE rowid=?", (rowid,)).fetchone()
		return self._row_to_stored(row) if row else None

	def unread_count(self) -> int:
		with self._lock:
			return self._conn.execute("SELECT COUNT(*) FROM messages WHERE deleted=0 AND read=0").fetchone()[0]

	def mark_read(self, rowids, read=True) -> None:
		with self._lock:
			self._conn.executemany("UPDATE messages SET read=? WHERE rowid=?", [(int(read), r) for r in rowids])
			self._conn.commit()

	def mark_all_read(self, server=None, topic=None) -> None:
		sql = "UPDATE messages SET read=1 WHERE read=0"
		args = []
		if server is not None:
			sql += " AND server=?"
			args.append(normalize_server(server))
		if topic is not None:
			sql += " AND topic=?"
			args.append(topic)
		with self._lock:
			self._conn.execute(sql, args)
			self._conn.commit()

	def delete_messages(self, rowids) -> None:
		with self._lock:
			self._conn.executemany("DELETE FROM messages WHERE rowid=?", [(r,) for r in rowids])
			self._conn.commit()

	def clear_history(self, server=None, topic=None) -> None:
		sql = "DELETE FROM messages WHERE 1=1"
		args = []
		if server is not None:
			sql += " AND server=?"
			args.append(normalize_server(server))
		if topic is not None:
			sql += " AND topic=?"
			args.append(topic)
		with self._lock:
			self._conn.execute(sql, args)
			self._conn.commit()

	def purge_older_than(self, days: int) -> int:
		if days <= 0:
			return 0
		limit = int(time.time()) - days * 86400
		with self._lock:
			cur = self._conn.execute("DELETE FROM messages WHERE time < ?", (limit,))
			self._conn.commit()
			return cur.rowcount

	def set_local_attachment(self, rowid: int, path: str) -> None:
		with self._lock:
			self._conn.execute("UPDATE messages SET local_attachment=? WHERE rowid=?", (path, rowid))
			self._conn.commit()
