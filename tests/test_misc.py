import time

from notificador import formatting, toasts
from notificador.ntfy.models import Message
from notificador.ntfy.subscriber import EndpointConfig, _Connection


def test_priority_options():
	assert toasts.priority_options(1) == {
		"silent": True,
		"suppress_popup": True,
		"long": False,
		"scenario": "default",
		"sound": "default",
	}
	assert toasts.priority_options(2)["silent"] and not toasts.priority_options(2)["suppress_popup"]
	assert toasts.priority_options(3)["sound"] == "default"
	assert toasts.priority_options(4)["long"]
	assert toasts.priority_options(5)["scenario"] == "urgent"
	assert toasts.priority_options(None)["sound"] == "default"


def test_toast_tag_is_stable_and_short():
	tag, group = toasts.toast_tag("https://ntfy.sh", "t", "s")
	assert tag == toasts.toast_tag("https://ntfy.sh", "t", "s")[0]
	assert len(tag) <= 64 and len(group) <= 64
	assert tag != toasts.toast_tag("https://ntfy.sh", "t", "otro")[0]


def test_parse_arguments():
	assert toasts.parse_arguments("row=5;a=1") == {"row": "5", "a": "1"}
	assert toasts.parse_arguments("") == {}


def test_tag_emojis_and_content():
	emojis, labels = formatting.split_tags(["warning", "servidor", "+1"])
	assert emojis == ["⚠️", "👍"] and labels == ["servidor"]
	msg = Message.from_dict(
		{
			"id": "x",
			"time": int(time.time()),
			"topic": "t",
			"title": "Hola",
			"message": "Cuerpo\nsegunda",
			"tags": ["warning", "servidor"],
			"priority": 5,
		},
		"https://ntfy.sh",
	)
	assert formatting.display_title(msg) == "⚠️ Hola"
	cols = formatting.list_columns(msg, False)
	assert cols[0] == "⚠️ Hola" and cols[1] == "Cuerpo" and cols[2] == "Urgente" and cols[5] == "No leída"
	text = formatting.content_text(msg)
	assert "Prioridad: Urgente (5)" in text and "Etiquetas: ⚠️ servidor" in text


class FakeManager:
	def __init__(self):
		self.messages = []
		self.statuses = []

	def _dispatch(self, msg, backlog):
		self.messages.append((msg, backlog))

	def _set_status(self, status):
		self.statuses.append(status)


def test_connection_url_and_since():
	cfg = EndpointConfig(server="https://ntfy.sh", topics={"b": 100, "a": 200}, auth="Bearer x")
	conn = _Connection(FakeManager(), cfg)
	url = conn._url("ws")
	assert url.startswith("wss://ntfy.sh/a,b/ws?") and "since=100" in url
	assert conn._headers() == {"Authorization": "Bearer x"}
	conn.use_query_auth = True
	assert "auth=" in conn._url("json") and conn._headers() == {}
	conn.server_time = 500
	assert "since=495" in conn._url("ws")
	cfg_all = EndpointConfig(server="http://local:8080/base", topics={"t": 0})
	assert _Connection(FakeManager(), cfg_all)._url("ws") == "ws://local:8080/base/t/ws?since=all"


def test_connection_handles_events_and_floor():
	manager = FakeManager()
	cfg = EndpointConfig(server="https://ntfy.sh", topics={"a": 100, "b": 0})
	conn = _Connection(manager, cfg)
	conn.connect_time = 1000
	conn._handle_line('{"id":"o","time":1000,"event":"open","topic":"a,b"}', "websocket")
	assert manager.statuses[-1].state == "connected"
	conn._handle_line('{"id":"k","time":1010,"event":"keepalive","topic":"a,b"}', "websocket")
	assert conn.server_time == 1010
	conn._handle_line('{"id":"old","time":50,"event":"message","topic":"a"}', "websocket")
	assert manager.messages == []  # anterior al suelo del tema «a»
	conn._handle_line('{"id":"old2","time":50,"event":"message","topic":"b"}', "websocket")
	assert manager.messages[-1][0].id == "old2" and manager.messages[-1][1] is True  # atrasado
	conn._handle_line('{"id":"n","time":1005,"event":"message","topic":"a"}', "websocket")
	assert manager.messages[-1][1] is False
	conn._handle_line('{"id":"d","time":1006,"event":"message_delete","topic":"a","sequence_id":"n"}', "websocket")
	assert manager.messages[-1][0].event == "message_delete"
	conn._handle_line("no es json", "websocket")
	assert len(manager.messages) == 3
