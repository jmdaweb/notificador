import json

import pytest

from notificador.config import Config, normalize_server
from notificador.ntfy.models import Message
from notificador.storage import Storage
from notificador.vault import Vault, VaultLockedError, WrongPasswordError


def make_msg(msg_id, time, topic="t", seq="", **extra):
	data = {"id": msg_id, "time": time, "event": "message", "topic": topic, "message": f"m{msg_id}", **extra}
	if seq:
		data["sequence_id"] = seq
	return Message.from_dict(data, "https://ntfy.sh")


def test_storage_dedupe_and_query(data_dir):
	st = Storage()
	rowid, replaced = st.add_message(make_msg("a", 10, tags=["x"]))
	assert rowid and replaced == []
	assert st.add_message(make_msg("a", 10)) == (None, [])
	st.add_message(make_msg("b", 20))
	items = st.query_messages()
	assert [i.msg.id for i in items] == ["b", "a"]
	assert st.unread_count() == 2
	st.mark_read([items[0].rowid])
	assert [i.msg.id for i in st.query_messages(unread_only=True)] == ["a"]
	assert [i.msg.id for i in st.query_messages(text="mb")] == ["b"]
	assert st.query_messages(server="https://ntfy.sh", topic="otro") == []
	st.close()


def test_storage_sequence_replace_delete_clear(data_dir):
	st = Storage()
	first, _ = st.add_message(make_msg("a", 10))
	# Actualización usando el id del primer mensaje como sequence_id
	second, replaced = st.add_message(make_msg("b", 20, seq="a"))
	assert replaced == [first]
	assert [i.rowid for i in st.query_messages()] == [second]
	third, replaced = st.add_message(make_msg("c", 30, seq="a"))
	assert replaced == [second]
	assert st.apply_clear("https://ntfy.sh", "t", "a") == [third]
	assert st.query_messages()[0].read
	assert st.apply_delete("https://ntfy.sh", "t", "a") == [third]
	assert st.query_messages() == []
	st.close()


def test_storage_subscriptions(data_dir):
	st = Storage()
	sub_id = st.add_subscription("ntfy.sh/", "t", alias="Alias")
	sub = st.get_subscription(sub_id)
	assert sub.server == "https://ntfy.sh" and sub.display_name == "Alias"
	st.set_last_seen("https://ntfy.sh", "t", "id1", 100)
	st.set_last_seen("https://ntfy.sh", "t", "id0", 50)  # más antiguo: se ignora
	assert st.find_subscription("https://ntfy.sh", "t").last_time == 100
	st.add_message(make_msg("a", 10))
	st.delete_subscription(sub_id, delete_messages=True)
	assert st.subscriptions() == [] and st.query_messages() == []
	st.close()


def test_storage_attachment_roundtrip(data_dir):
	st = Storage()
	rowid, _ = st.add_message(make_msg("a", 10, attachment={"name": "f.txt", "url": "https://x/f.txt", "size": 3}))
	st.set_local_attachment(rowid, "C:/tmp/f.txt")
	stored = st.get_message(rowid)
	assert stored.msg.attachment.name == "f.txt" and stored.local_attachment == "C:/tmp/f.txt"
	st.close()


def test_vault_roundtrip(data_dir):
	vault = Vault()
	assert not vault.exists()
	vault.create("secreto1")
	vault.set("ntfy.sh", "basic", "ana", "clave")
	vault.set("https://otro.example/", "token", "", "tk_1")
	raw = vault.path.read_text(encoding="utf-8")
	assert "clave" not in raw and "tk_1" not in raw and "ana" not in raw

	again = Vault()
	assert again.get("https://ntfy.sh") is None  # bloqueado
	with pytest.raises(VaultLockedError):
		again.set("x", "basic")
	with pytest.raises(WrongPasswordError):
		again.unlock("incorrecta")
	again.unlock("secreto1")
	assert again.get("https://ntfy.sh") == {"type": "basic", "username": "ana", "secret": "clave"}
	assert again.servers() == ["https://ntfy.sh", "https://otro.example"]
	again.change_password("nueva123")
	third = Vault()
	with pytest.raises(WrongPasswordError):
		third.unlock("secreto1")
	third.unlock("nueva123")
	third.delete("https://ntfy.sh")
	assert third.servers() == ["https://otro.example"]


def test_config_recent(data_dir):
	cfg = Config()
	cfg.add_recent_server("mi.servidor/")
	cfg.add_recent_topic("mi.servidor", "uno")
	cfg.add_recent_topic("https://mi.servidor", "dos")
	cfg.add_recent_topic("https://mi.servidor", "uno")
	again = Config()
	assert again.recent_servers()[0] == "https://mi.servidor"
	assert again.recent_topics("mi.servidor") == ["uno", "dos"]
	assert json.loads(cfg.path.read_text(encoding="utf-8"))["recent_servers"][0] == "https://mi.servidor"
	again.remove_recent_server("mi.servidor")
	assert "https://mi.servidor" not in again.recent_servers()


def test_normalize_server():
	assert normalize_server(" ntfy.sh/ ") == "https://ntfy.sh"
	assert normalize_server("http://localhost:8080/") == "http://localhost:8080"
	assert normalize_server("") == ""
