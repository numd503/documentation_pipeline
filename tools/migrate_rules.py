"""Перенос файлов правил в секционный формат.

Было — файл на язык, каждый со своим `ruleset_version`, `exclude` и `rules`
на верхнем уровне. Стало — один файл на проект, по секции на шаг:

    version: "1"

    dotnet:
      ruleset_version: …
      exclude: …
      rules: …

    web:
      …

Перенос **текстовый**, а не через `yaml.safe_load` + `yaml.dump`. Круговой
прогон через загрузчик потерял бы все комментарии, а в настроенном наборе
правил комментарий объясняет, почему решение принято именно такое, — то есть
ровно то, ради чего файл и читают. Поэтому строки сдвигаются на два пробела,
и больше с ними не делается ничего.

    uv run python tools/migrate_rules.py --dotnet rules.yaml --out rules.yaml
    uv run python tools/migrate_rules.py --dotnet rules.yaml --web web.yaml --out rules.yaml

Идемпотентен: файл, уже перенесённый, скрипт распознаёт по наличию секции
и отказывается трогать. Иначе повторный запуск завернул бы секции в секции.
"""

import argparse
import sys
from pathlib import Path

HEADER = """# Правила классификации: один файл, по секции на шаг.
#
# `dotnet` читают `scan`, `symbols` и `validate`; `web` — `web scan`. Секцию
# называет вызывающий, и умолчания у неё нет: плоский файл, прочитанный шагом
# `web`, дал бы .NET-правила на TypeScript, а `require_public: true` отсеял бы
# весь фронт разом — без единого сообщения об ошибке.

version: "1"
"""

SECTIONS = ("dotnet", "web")


def indent(text: str, section: str) -> str:
    """Завернуть содержимое файла в секцию, сохранив комментарии.

    Строка `version:` верхнего уровня отбрасывается: версия формата общая
    для файла и уже написана в шапке. Пустые строки остаются пустыми —
    сдвинутая пустая строка дала бы хвостовые пробелы в каждом абзаце.
    """
    lines = []
    for line in text.splitlines():
        if line.startswith("version:"):
            continue
        lines.append(f"  {line}" if line.strip() else "")

    body = "\n".join(lines).strip("\n")
    return f"{section}:\n{body}\n"


def already_sectioned(text: str) -> str | None:
    """Имя секции, если файл уже перенесён."""
    return next(
        (name for name in SECTIONS if text.startswith(f"{name}:") or f"\n{name}:" in text), None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotnet", type=Path, help="Плоский файл правил .NET.")
    parser.add_argument("--web", type=Path, help="Плоский файл правил фронта.")
    parser.add_argument("--out", type=Path, required=True, help="Куда записать результат.")
    args = parser.parse_args()

    if not args.dotnet and not args.web:
        print("нужен хотя бы один из --dotnet и --web", file=sys.stderr)
        return 2

    parts = [HEADER]
    for section in SECTIONS:
        source = getattr(args, section)
        if source is None:
            continue
        text = source.read_text(encoding="utf-8")
        existing = already_sectioned(text)
        if existing:
            print(
                f"{source}: файл уже секционный (есть `{existing}:`), перенос не нужен",
                file=sys.stderr,
            )
            return 1
        parts.append(indent(text, section))

    args.out.write_text("\n".join(parts), encoding="utf-8")
    print(f"Записано: {args.out}")
    print("Проверьте: uv run docpipe scan --root . --stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
