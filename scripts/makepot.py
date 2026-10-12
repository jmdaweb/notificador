"""Genera locale/notificador.pot con las cadenas marcadas con _() y ngettext().

Uso: python scripts/makepot.py
Para traducir: copia el .pot a locale/<idioma>/LC_MESSAGES/notificador.po, tradúcelo y
compílalo a notificador.mo (por ejemplo con «msgfmt» o Poedit).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def escape(text: str) -> str:
	return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\n").replace("\t", "\t")


def main() -> None:
	entries: dict[tuple, list[str]] = {}
	for path in sorted((ROOT / "notificador").rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
				continue
			args = node.args
			if node.func.id == "_" and args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
				key = (args[0].value,)
			elif node.func.id == "ngettext" and len(args) >= 2 and all(
				isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args[:2]
			):
				key = (args[0].value, args[1].value)
			else:
				continue
			entries.setdefault(key, []).append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
	lines = [
		'msgid ""',
		'msgstr ""',
		'"Content-Type: text/plain; charset=UTF-8\n"',
		'"Content-Transfer-Encoding: 8bit\n"',
		'"Language: es\n"',
		"",
	]
	for key, refs in entries.items():
		lines.append("#: " + " ".join(refs))
		if "{" in key[0]:
			lines.append("#, python-brace-format")
		lines.append(f'msgid "{escape(key[0])}"')
		if len(key) == 2:
			lines.append(f'msgid_plural "{escape(key[1])}"')
			lines.append('msgstr[0] ""')
			lines.append('msgstr[1] ""')
		else:
			lines.append('msgstr ""')
		lines.append("")
	out = ROOT / "locale" / "notificador.pot"
	out.parent.mkdir(exist_ok=True)
	out.write_text("\n".join(lines), encoding="utf-8")
	print(f"{len(entries)} cadenas escritas en {out}")


if __name__ == "__main__":
	main()
