"""Modelos de datos del protocolo ntfy."""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

TOPIC_RE = re.compile(r"^[-_A-Za-z0-9]{1,64}$")
MAX_ACTIONS = 3
MAX_MESSAGE_BYTES = 4096
MAX_TITLE_BYTES = 1024
MAX_TAGS_BYTES = 512

ACTION_TYPES = ("view", "http", "broadcast", "copy")

PRIORITY_NAMES = {
	1: "min",
	2: "low",
	3: "default",
	4: "high",
	5: "urgent",
}


def is_valid_topic(topic: str) -> bool:
	return bool(TOPIC_RE.match(topic or ""))


@dataclass
class Action:
	action: str
	label: str
	url: str = ""
	clear: bool = False
	method: str = ""
	headers: dict = field(default_factory=dict)
	body: str = ""
	intent: str = ""
	extras: dict = field(default_factory=dict)
	value: str = ""
	id: str = ""

	@classmethod
	def from_dict(cls, data: dict) -> "Action":
		return cls(
			action=str(data.get("action", "")),
			label=str(data.get("label", "")),
			url=str(data.get("url") or ""),
			clear=bool(data.get("clear", False)),
			method=str(data.get("method") or ""),
			headers=dict(data.get("headers") or {}),
			body=str(data.get("body") or ""),
			intent=str(data.get("intent") or ""),
			extras=dict(data.get("extras") or {}),
			value=str(data.get("value") or ""),
			id=str(data.get("id") or ""),
		)

	def to_dict(self) -> dict:
		"""Representación JSON para publicar: solo los campos relevantes para el tipo."""
		out: dict[str, Any] = {"action": self.action, "label": self.label}
		if self.action in ("view", "http") and self.url:
			out["url"] = self.url
		if self.action == "http":
			if self.method:
				out["method"] = self.method.upper()
			if self.headers:
				out["headers"] = dict(self.headers)
			if self.body:
				out["body"] = self.body
		if self.action == "broadcast":
			if self.intent:
				out["intent"] = self.intent
			if self.extras:
				out["extras"] = dict(self.extras)
		if self.action == "copy":
			out["value"] = self.value
		if self.clear:
			out["clear"] = True
		return out

	def validate(self) -> str | None:
		"""Devuelve un mensaje de error (sin traducir) o None."""
		if self.action not in ACTION_TYPES:
			return "invalid_type"
		if not self.label.strip():
			return "missing_label"
		if self.action in ("view", "http") and not self.url.strip():
			return "missing_url"
		if self.action == "copy" and not self.value:
			return "missing_value"
		return None


@dataclass
class Attachment:
	name: str = ""
	url: str = ""
	type: str = ""
	size: int = 0
	expires: int = 0

	@classmethod
	def from_dict(cls, data: dict | None) -> "Attachment | None":
		if not data:
			return None
		return cls(
			name=str(data.get("name") or ""),
			url=str(data.get("url") or ""),
			type=str(data.get("type") or ""),
			size=int(data.get("size") or 0),
			expires=int(data.get("expires") or 0),
		)


@dataclass
class Message:
	"""Evento recibido de un servidor ntfy."""

	id: str
	time: int
	event: str
	topic: str
	server: str = ""
	expires: int = 0
	message: str = ""
	title: str = ""
	tags: list = field(default_factory=list)
	priority: int = 3
	click: str = ""
	icon: str = ""
	actions: list = field(default_factory=list)
	attachment: Attachment | None = None
	sequence_id: str = ""
	content_type: str = ""
	raw: dict = field(default_factory=dict)

	@classmethod
	def from_dict(cls, data: dict, server: str = "") -> "Message":
		actions = []
		for item in data.get("actions") or []:
			if isinstance(item, dict):
				actions.append(Action.from_dict(item))
		try:
			priority = int(data.get("priority") or 3)
		except (TypeError, ValueError):
			priority = 3
		priority = min(5, max(1, priority))
		return cls(
			id=str(data.get("id") or ""),
			time=int(data.get("time") or 0),
			event=str(data.get("event") or "message"),
			topic=str(data.get("topic") or ""),
			server=server,
			expires=int(data.get("expires") or 0),
			message=str(data.get("message") or ""),
			title=str(data.get("title") or ""),
			tags=[str(t) for t in (data.get("tags") or [])],
			priority=priority,
			click=str(data.get("click") or ""),
			icon=str(data.get("icon") or ""),
			actions=actions,
			attachment=Attachment.from_dict(data.get("attachment")),
			sequence_id=str(data.get("sequence_id") or ""),
			content_type=str(data.get("content_type") or ""),
			raw=dict(data),
		)

	@classmethod
	def from_json(cls, text: str, server: str = "") -> "Message":
		return cls.from_dict(json.loads(text), server)

	@property
	def is_markdown(self) -> bool:
		return self.content_type == "text/markdown"

	@property
	def effective_sequence_id(self) -> str:
		"""ntfy usa el id del mensaje como sequence_id si no se indica otro."""
		return self.sequence_id or self.id


@dataclass
class PublishRequest:
	server: str
	topic: str
	message: str = ""
	title: str = ""
	priority: int = 3
	tags: list = field(default_factory=list)
	markdown: bool = False
	click: str = ""
	icon: str = ""
	attach_url: str = ""
	file_path: str = ""
	filename: str = ""
	actions: list = field(default_factory=list)
	delay: str = ""
	timezone: str = ""
	email: str = ""
	call: str = ""
	sequence_id: str = ""
	cache: bool = True
	firebase: bool = True
	unifiedpush: bool = False
	template: str = ""

	def to_dict(self) -> dict:
		data = asdict(self)
		data["actions"] = [a.to_dict() if isinstance(a, Action) else a for a in self.actions]
		return data

	@classmethod
	def from_dict(cls, data: dict) -> "PublishRequest":
		known = {f for f in cls.__dataclass_fields__}
		values = {k: v for k, v in data.items() if k in known}
		values["actions"] = [Action.from_dict(a) for a in values.get("actions") or []]
		return cls(**values)
