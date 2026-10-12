import base64
import json

import pytest

from notificador.ntfy import publisher
from notificador.ntfy.models import Action, PublishRequest


def test_auth_headers():
	assert publisher.auth_header(None) is None
	basic = publisher.auth_header({"type": "basic", "username": "testuser", "secret": "fakepassword"})
	assert basic == "Basic dGVzdHVzZXI6ZmFrZXBhc3N3b3Jk"
	assert publisher.auth_header({"type": "token", "secret": "tk_123"}) == "Bearer tk_123"
	assert publisher.auth_header({"type": "token", "secret": ""}) is None


def test_auth_query_value_matches_docs():
	# Ejemplo de la documentación de ntfy
	value = publisher.auth_query_value("Basic dGVzdHVzZXI6ZmFrZXBhc3N3b3Jk")
	assert value == "QmFzaWMgZEdWemRIVnpaWEk2Wm1GclpYQmhjM04zYjNKaw"
	assert "=" not in value
	padded = value + "=" * (-len(value) % 4)
	assert base64.urlsafe_b64decode(padded) == b"Basic dGVzdHVzZXI6ZmFrZXBhc3N3b3Jk"


def test_build_json_request_with_everything():
	req = PublishRequest(
		server="ntfy.example.com/",
		topic="alertas",
		message="Hola ñandú 🙂",
		title="Título",
		priority=5,
		tags=["warning", " skull ", ""],
		markdown=True,
		click="https://c",
		icon="https://i.png",
		attach_url="https://a/f.pdf",
		filename="f.pdf",
		actions=[Action(action="view", label="Abrir", url="https://x")],
		delay="30m",
		timezone="Europe/Madrid",
		email="a@b.c",
		call="yes",
		sequence_id="s1",
		cache=False,
		firebase=False,
	)
	http = publisher.build_request(req, "Bearer t")
	assert http.method == "POST"
	assert http.url == "https://ntfy.example.com/"
	assert http.headers["Authorization"] == "Bearer t"
	assert http.headers["X-Cache"] == "no" and http.headers["X-Firebase"] == "no"
	body = http.json_body
	assert body["topic"] == "alertas" and body["message"] == "Hola ñandú 🙂"
	assert body["priority"] == 5 and body["tags"] == ["warning", "skull"]
	assert body["markdown"] is True and body["attach"] == "https://a/f.pdf"
	assert body["actions"] == [{"action": "view", "label": "Abrir", "url": "https://x"}]
	for key in ("click", "icon", "filename", "delay", "timezone", "email", "call", "sequence_id"):
		assert key in body


def test_build_json_request_defaults_are_omitted():
	http = publisher.build_request(PublishRequest(server="https://ntfy.sh", topic="t", message="m"))
	assert http.json_body == {"topic": "t", "message": "m"}
	assert "Authorization" not in http.headers


def test_build_file_request(tmp_path):
	path = tmp_path / "informe.txt"
	path.write_text("contenido", encoding="utf-8")
	req = PublishRequest(
		server="https://ntfy.sh",
		topic="t",
		message="Mensaje con acentos áé",
		title="T",
		priority=2,
		file_path=str(path),
		actions=[Action(action="copy", label="C", value="v")],
		sequence_id="s",
	)
	http = publisher.build_request(req)
	assert http.method == "PUT" and http.url == "https://ntfy.sh/t"
	assert http.file_path == str(path)
	assert http.params["message"] == "Mensaje con acentos áé"
	assert http.params["filename"] == "informe.txt"
	assert http.params["priority"] == "2" and http.params["sid"] == "s"
	assert json.loads(http.params["actions"]) == [{"action": "copy", "label": "C", "value": "v"}]


def test_build_template_request():
	req = PublishRequest(server="https://ntfy.sh", topic="t", message='{"a": 1}', template="github")
	http = publisher.build_request(req)
	assert http.method == "POST" and http.url == "https://ntfy.sh/t"
	assert http.headers["X-Template"] == "github"
	assert http.text_body == '{"a": 1}'


def test_validate_request():
	errors = publisher.validate_request(PublishRequest(server="", topic="mal tema"))
	assert {e[0] for e in errors} == {"server", "topic"}
	req = PublishRequest(
		server="https://ntfy.sh",
		topic="ok",
		actions=[Action(action="view", label=str(i), url="u") for i in range(4)],
	)
	assert ("actions", "Se admiten como máximo 3 acciones.") in publisher.validate_request(req)
	both = PublishRequest(server="https://ntfy.sh", topic="ok", file_path="x", attach_url="y")
	assert any(e[0] == "attach" for e in publisher.validate_request(both))
	assert publisher.validate_request(PublishRequest(server="https://ntfy.sh", topic="ok")) == []


def test_message_too_long():
	assert not publisher.message_too_long(PublishRequest(server="s", topic="t", message="a" * 4096))
	assert publisher.message_too_long(PublishRequest(server="s", topic="t", message="ñ" * 2049))


def test_default_limits():
	assert publisher.default_limits("https://ntfy.sh").file_size == 2 * 1024 * 1024
	assert publisher.default_limits("https://mio.example").file_size == 15 * 1024 * 1024


def test_parse_account_limits():
	doc = {
		"limits": {"attachment_file_size": 2097152, "attachment_total_size": 20971520, "attachment_expiry_duration": 10800},
		"stats": {"attachment_total_size_remaining": 1000},
	}
	limits = publisher.parse_account_limits(doc, "https://ntfy.sh")
	assert limits.file_size == 2097152 and limits.total_remaining == 1000 and limits.from_server
	assert not publisher.parse_account_limits({}, "https://x").from_server


def test_validate_file(tmp_path):
	limits = publisher.AttachmentLimits(file_size=10, total_remaining=8, expiry=0, from_server=True)
	assert publisher.validate_file(str(tmp_path / "no"), limits) == "El archivo no existe."
	empty = tmp_path / "vacio"
	empty.write_bytes(b"")
	assert publisher.validate_file(str(empty), limits) == "El archivo está vacío."
	big = tmp_path / "grande"
	big.write_bytes(b"x" * 11)
	assert "límite" in publisher.validate_file(str(big), limits)
	quota = tmp_path / "cuota"
	quota.write_bytes(b"x" * 9)
	assert "cuota" in publisher.validate_file(str(quota), limits)
	ok = tmp_path / "ok"
	ok.write_bytes(b"x" * 5)
	assert publisher.validate_file(str(ok), limits) is None
	disabled = publisher.AttachmentLimits(0, 0, 0, True)
	assert publisher.validate_file(str(ok), disabled) == "El servidor no admite adjuntos."


def test_format_size():
	assert publisher.format_size(500) == "500 B"
	assert publisher.format_size(2 * 1024 * 1024) == "2 MB"
	assert publisher.format_size(1536) == "1.5 KB"


class FakeResponse:
	def __init__(self, status, payload):
		self.status_code = status
		self._payload = payload
		self.ok = 200 <= status < 300
		self.text = json.dumps(payload)

	def json(self):
		return self._payload


class FakeSession:
	def __init__(self, response):
		self.response = response
		self.calls = []

	def request(self, method, url, **kwargs):
		data = kwargs.get("data")
		if hasattr(data, "read"):
			chunks = []
			while True:
				chunk = data.read(1024)
				if not chunk:
					break
				chunks.append(chunk)
			kwargs["data"] = b"".join(chunks)
		self.calls.append((method, url, kwargs))
		return self.response


def test_publish_success_and_progress(tmp_path):
	path = tmp_path / "f.bin"
	path.write_bytes(b"x" * 200_000)
	session = FakeSession(FakeResponse(200, {"id": "nuevo", "time": 5, "event": "message", "topic": "t"}))
	progress = []
	req = PublishRequest(server="https://ntfy.sh", topic="t", file_path=str(path))
	msg = publisher.publish(req, progress=lambda sent, total: progress.append((sent, total)), session=session)
	assert msg.id == "nuevo" and msg.server == "https://ntfy.sh"
	method, url, kwargs = session.calls[0]
	assert method == "PUT" and len(kwargs["data"]) == 200_000
	assert progress[-1] == (200_000, 200_000)


def test_publish_error_message():
	session = FakeSession(FakeResponse(413, {"code": 41301, "http": 413, "error": "too large"}))
	with pytest.raises(publisher.PublishError) as info:
		publisher.publish(PublishRequest(server="https://ntfy.sh", topic="t", message="m"), session=session)
	assert info.value.status == 413 and "too large" in str(info.value)


def test_publish_cancel(tmp_path):
	import threading

	path = tmp_path / "f.bin"
	path.write_bytes(b"x" * 10)
	cancel = threading.Event()
	cancel.set()
	session = FakeSession(FakeResponse(200, {}))
	with pytest.raises(publisher.PublishCancelled):
		publisher.publish(
			PublishRequest(server="https://ntfy.sh", topic="t", file_path=str(path)), cancel=cancel, session=session
		)


def test_sequence_operations():
	session = FakeSession(FakeResponse(200, {}))
	publisher.clear_notification("https://ntfy.sh", "t", "s1", session=session)
	publisher.delete_notification("https://ntfy.sh", "t", "s1", session=session)
	assert session.calls[0][:2] == ("PUT", "https://ntfy.sh/t/s1/clear")
	assert session.calls[1][:2] == ("DELETE", "https://ntfy.sh/t/s1")
