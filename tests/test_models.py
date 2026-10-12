import json

from notificador.ntfy.models import Action, Message, PublishRequest, is_valid_topic


def test_parse_full_message():
	raw = {
		"id": "abc123",
		"time": 1700000000,
		"expires": 1700043200,
		"event": "message",
		"topic": "alertas",
		"title": "Disco lleno",
		"message": "Queda un 5 %",
		"priority": 5,
		"tags": ["warning", "servidor"],
		"click": "https://example.com",
		"icon": "https://example.com/i.png",
		"actions": [
			{"id": "a1", "action": "view", "label": "Abrir", "url": "https://example.com", "clear": True},
			{"action": "http", "label": "Reiniciar", "url": "https://api/x", "method": "PUT", "headers": {"A": "b"}},
		],
		"attachment": {"name": "log.txt", "url": "https://ntfy.sh/file/x.txt", "type": "text/plain", "size": 12, "expires": 1},
		"sequence_id": "seq1",
		"content_type": "text/markdown",
		"campo_futuro": 42,
	}
	msg = Message.from_json(json.dumps(raw), "https://ntfy.sh")
	assert msg.id == "abc123" and msg.topic == "alertas" and msg.priority == 5
	assert msg.tags == ["warning", "servidor"]
	assert msg.actions[0].clear is True and msg.actions[1].method == "PUT"
	assert msg.attachment.name == "log.txt" and msg.attachment.size == 12
	assert msg.is_markdown
	assert msg.effective_sequence_id == "seq1"
	assert msg.raw["campo_futuro"] == 42


def test_parse_minimal_and_invalid_priority():
	msg = Message.from_dict({"id": "x", "time": 1, "event": "message", "topic": "t", "priority": "zzz"})
	assert msg.priority == 3
	assert msg.effective_sequence_id == "x"
	assert msg.attachment is None
	assert Message.from_dict({"id": "y", "priority": 9}).priority == 5


def test_delete_event():
	msg = Message.from_dict({"id": "d", "time": 2, "event": "message_delete", "topic": "t", "sequence_id": "s"})
	assert msg.event == "message_delete" and msg.effective_sequence_id == "s"


def test_topic_validation():
	assert is_valid_topic("mi_tema-1")
	assert not is_valid_topic("")
	assert not is_valid_topic("con espacio")
	assert not is_valid_topic("x" * 65)
	assert not is_valid_topic("ñandú")


def test_action_to_dict_only_relevant_fields():
	action = Action(action="copy", label="Copiar", url="ignorada", value="123", method="GET")
	assert action.to_dict() == {"action": "copy", "label": "Copiar", "value": "123"}
	http = Action(action="http", label="Ir", url="https://x", method="get", headers={"K": "v"}, body="b", clear=True)
	assert http.to_dict() == {
		"action": "http",
		"label": "Ir",
		"url": "https://x",
		"method": "GET",
		"headers": {"K": "v"},
		"body": "b",
		"clear": True,
	}


def test_action_validation():
	assert Action(action="view", label="", url="https://x").validate() == "missing_label"
	assert Action(action="view", label="a").validate() == "missing_url"
	assert Action(action="copy", label="a").validate() == "missing_value"
	assert Action(action="broadcast", label="a").validate() is None
	assert Action(action="otra", label="a").validate() == "invalid_type"


def test_publish_request_roundtrip():
	req = PublishRequest(server="https://ntfy.sh", topic="t", actions=[Action(action="view", label="a", url="u")])
	again = PublishRequest.from_dict(req.to_dict())
	assert again == req
