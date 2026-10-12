"""Representación textual de las notificaciones (lista, panel de contenido y toasts)."""

import time

try:
	import emoji as _emoji
except ImportError:  # pragma: no cover
	_emoji = None

from .i18n import _
from .ntfy.models import Message
from .ntfy.publisher import format_size


def priority_name(priority: int) -> str:
	return {
		1: _("Mínima"),
		2: _("Baja"),
		3: _("Normal"),
		4: _("Alta"),
		5: _("Urgente"),
	}.get(int(priority or 3), _("Normal"))


def tag_emoji(tag: str) -> str | None:
	"""Emoji asociado a una etiqueta de ntfy (códigos cortos como «warning» o «+1»), o None."""
	if _emoji is None or not tag:
		return None
	code = f":{tag}:"
	result = _emoji.emojize(code, language="alias")
	return result if result != code else None


def split_tags(tags: list[str]) -> tuple[list[str], list[str]]:
	"""Separa las etiquetas en (emojis, etiquetas de texto) igual que hacen los clientes de ntfy."""
	emojis, labels = [], []
	for tag in tags:
		symbol = tag_emoji(tag)
		if symbol:
			emojis.append(symbol)
		else:
			labels.append(tag)
	return emojis, labels


def format_time(timestamp: int, with_date=True) -> str:
	if not timestamp:
		return ""
	local = time.localtime(timestamp)
	today = time.localtime()
	if not with_date or (local.tm_year, local.tm_yday) == (today.tm_year, today.tm_yday):
		return time.strftime("%H:%M", local)
	return time.strftime("%d/%m/%Y %H:%M", local)


def first_line(text: str, limit: int = 120) -> str:
	line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
	return line if len(line) <= limit else line[: limit - 1] + "…"


def display_title(msg: Message) -> str:
	"""Título con los emojis de las etiquetas delante (o el tema si no hay título)."""
	emojis, _labels = split_tags(msg.tags)
	title = msg.title or msg.topic
	return (" ".join(emojis) + " " + title).strip() if emojis else title


def list_columns(msg: Message, read: bool) -> list[str]:
	"""Columnas de la lista principal: Título, Mensaje, Prioridad, Tema, Fecha, Estado."""
	title = display_title(msg) if msg.title or msg.tags else first_line(msg.message, 80) or msg.topic
	return [
		title,
		first_line(msg.message),
		priority_name(msg.priority),
		msg.topic,
		format_time(msg.time),
		_("Leída") if read else _("No leída"),
	]


def content_text(msg: Message, local_attachment: str = "") -> str:
	"""Texto completo para el cuadro de contenido de solo lectura."""
	lines = []
	if msg.title:
		lines.append(display_title(msg))
		lines.append("")
	lines.append(msg.message or _("(sin mensaje)"))
	lines.append("")
	lines.append(_("Tema: {topic}").format(topic=msg.topic))
	lines.append(_("Servidor: {server}").format(server=msg.server))
	lines.append(_("Fecha: {date}").format(date=time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(msg.time or 0))))
	lines.append(_("Prioridad: {name} ({n})").format(name=priority_name(msg.priority), n=msg.priority))
	emojis, labels = split_tags(msg.tags)
	if msg.tags:
		tag_text = ", ".join(labels)
		if emojis:
			tag_text = (" ".join(emojis) + (" " + tag_text if tag_text else "")).strip()
		lines.append(_("Etiquetas: {tags}").format(tags=tag_text))
	if msg.click:
		lines.append(_("Enlace: {url}").format(url=msg.click))
	if msg.is_markdown:
		lines.append(_("Formato: Markdown"))
	if msg.attachment:
		att = msg.attachment
		parts = [att.name or _("adjunto")]
		if att.type:
			parts.append(att.type)
		if att.size:
			parts.append(format_size(att.size))
		lines.append(_("Adjunto: {info}").format(info=", ".join(parts)))
		if att.expires:
			lines.append(
				_("El adjunto caduca: {date}").format(
					date=time.strftime("%d/%m/%Y %H:%M", time.localtime(att.expires))
				)
			)
		if local_attachment:
			lines.append(_("Descargado en: {path}").format(path=local_attachment))
	if msg.actions:
		lines.append(_("Acciones: {labels}").format(labels=", ".join(a.label for a in msg.actions)))
	if msg.sequence_id:
		lines.append(_("ID de secuencia: {id}").format(id=msg.sequence_id))
	lines.append(_("ID: {id}").format(id=msg.id))
	return "\n".join(lines)


def toast_texts(msg: Message) -> tuple[str, str]:
	"""(cabecera, cuerpo) para la notificación de Windows."""
	header = display_title(msg)
	body = msg.message or ""
	if msg.attachment:
		extra = _("Adjunto: {name}").format(name=msg.attachment.name or _("archivo"))
		body = f"{body}\n{extra}" if body else extra
	return header, body
