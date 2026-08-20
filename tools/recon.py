#!/usr/bin/env python3
"""Разведка репозитория, который видишь впервые (R01).

Скрипт отвечает на пять вопросов и **больше ни на что**: чем собран репозиторий,
что читать первым, где его центр, чем он заякорен и как языки говорят между собой.
Блок, для которого нельзя назвать вопрос, из скрипта удаляется — это правило
приёмки R01, а не пожелание: отчёт, печатающий «всё, что удалось собрать», —
второй способ читать репозиторий, причём худший.

ЧТО ТРЕБУЕТСЯ: `python3` и `git`. Ничего больше — ни одной внешней библиотеки.
Скрипт запускают в закрытом контуре, где `pip install` не проходит, поэтому
импорт `yaml` или `pydantic` означает «не запустился ни разу». По той же причине
здесь нет ни одного сетевого вызова.

ЧТО СКРИПТ ДЕЛАЕТ С РЕПОЗИТОРИЕМ: только читает. `git ls-files`, `git log`,
чтение файлов. Рабочее дерево не трогает, в индекс не пишет, `fetch` не зовёт.

ДЕТЕРМИНИЗМ. Окно археологии считается от даты коммита HEAD, а не от текущего
времени. Иначе один и тот же репозиторий даёт разные ответы во вторник и в среду,
и сравнить два прогона нельзя. Все списки сортируются явным ключом; порядок
обхода файловой системы источником порядка не является нигде.

Запуск:

    python3 tools/recon.py                       # человеку, на stdout
    python3 tools/recon.py --root ПУТЬ
    python3 tools/recon.py --json recon.json     # машине (вход R02 и R03)
    python3 tools/recon.py --json recon.json --text recon.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "docpipe.recon/1"

# Двоичное — отдельный «язык», а не «прочее». Считать в нём строки нельзя:
# `\n` в PNG встречается, и на abp такой счёт дал 2,4 млн «строк» картинок
# при 3,5 млн строк всего — то есть таблица языков говорила неправду
# о собственном репозитории в первом же блоке.
BINARY_LANG = "двоичные"

# ──────────────────────────────────────────────────────────────────────────────
# Пределы. Каждый существует, потому что без него ломается критерий приёмки:
# либо двухминутный бюджет на 17 тыс. файлов, либо читаемость отчёта.
# ──────────────────────────────────────────────────────────────────────────────

MAX_FILE_BYTES = 2 * 1024 * 1024  # файл крупнее не читается вовсе; попадает в блок 6
MAX_LITERALS_PER_FILE = 3000  # минифицированный JS даёт десятки тысяч литералов
MAX_IDS_PER_DATA_FILE = 5000
MAX_AVG_LINE_BYTES = 500  # длиннее — минифицированный бандл, а не исходник
MAX_LINE_CHARS = 2000  # одна такая строка — уже бандл, даже если средняя короткая
NAMESPACE_FANOUT_CAP = 50  # `using` в пространство из 500 файлов — не ребро, а шум
PAGERANK_ITERATIONS = 20
DEFAULT_TOP = 15
DEFAULT_MONTHS = 12
BUS_FACTOR_MIN_COMMITS = 5
BUS_FACTOR_SHARE = 0.8

# ──────────────────────────────────────────────────────────────────────────────
# Языки и раскладка. Расширение — единственный признак, который есть у файла
# до чтения; ошибка здесь стоит дёшево и видна в блоке 1 глазами.
# ──────────────────────────────────────────────────────────────────────────────

LANG_BY_EXT: dict[str, str] = {
    ".cs": "csharp",
    ".fs": "fsharp",
    ".vb": "vbnet",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".vue": "vue",
    ".py": "python",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
    ".scala": "scala",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".m": "objc",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".razor": "razor",
    ".cshtml": "razor",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".less": "css",
    ".xml": "xml",
    ".xaml": "xml",
    ".config": "xml",
    ".props": "xml",
    ".targets": "xml",
    ".csproj": "xml",
    ".slnx": "xml",
    ".fsproj": "xml",
    ".vbproj": "xml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".md": "markdown",
    ".rst": "markdown",
    ".txt": "text",
    ".proto": "proto",
    ".tf": "terraform",
}

# Языки, в которых ищутся строковые литералы, объявления и импорты. Разметка
# и данные сюда не входят: литерал из `.md` — это текст статьи, а не якорь.
CODE_LANGS = frozenset(
    {
        "csharp",
        "fsharp",
        "vbnet",
        "typescript",
        "javascript",
        "vue",
        "python",
        "java",
        "kotlin",
        "go",
        "ruby",
        "php",
        "rust",
        "scala",
        "swift",
        "c",
        "cpp",
        "objc",
        "razor",
        "powershell",
        "shell",
    }
)

DATA_LANGS = frozenset({"xml", "json", "yaml", "ini"})

# ──────────────────────────────────────────────────────────────────────────────
# Отсев. Каталоги вендоринга и сборки исключаются ПО СЕГМЕНТАМ пути, а не глобом.
# Причина записана в CLAUDE.md и проверена на реальном коде: `fnmatch` не понимает
# `**` как «ноль или больше сегментов», поэтому `**/obj/**` не ловит `obj/x.cs`
# в корне. Сравнение множеств сегментов от этой ловушки свободно структурно.
# ──────────────────────────────────────────────────────────────────────────────

EXCLUDED_SEGMENTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vs",
        ".vscode",
        "node_modules",
        "bower_components",
        "obj",
        "bin",
        "dist",
        "build",
        "out",
        "target",
        "coverage",
        "vendor",
        "third_party",
        "thirdparty",
        "packages",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nx",
        ".angular",
        ".next",
        ".nuxt",
        ".gradle",
        ".terraform",
    }
)

EXCLUDED_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".g.cs",
    ".designer.cs",
    ".generated.cs",
    "_pb2.py",
    "_pb2_grpc.py",
    ".pb.go",
    ".lock",
    ".map",
)

BINARY_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".psd",
        ".otf",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".7z",
        ".dll",
        ".jar",
        ".class",
        ".nupkg",
        ".xlsx",
        ".docx",
        ".pptx",
        ".exe",
        ".so",
        ".dylib",
        ".pdb",
        ".snk",
        ".mp4",
        ".mp3",
        ".wav",
        ".bin",
        ".dat",
        ".db",
        ".sqlite",
    }
)

# ──────────────────────────────────────────────────────────────────────────────
# Файлы сборки: что именно из них следует. Пара (имя → что это значит) —
# ответ на «чем собран репозиторий», а не список найденного.
# ──────────────────────────────────────────────────────────────────────────────

BUILD_FILES: tuple[tuple[str, str, str], ...] = (
    ("*.sln", "решение .NET (классический формат)", "dotnet"),
    ("*.slnx", "решение .NET (XML-формат, VS 17.10+)", "dotnet"),
    ("*.csproj", "проект .NET (C#)", "dotnet"),
    ("*.fsproj", "проект .NET (F#)", "dotnet"),
    ("*.vbproj", "проект .NET (VB)", "dotnet"),
    ("Directory.Build.props", "общие свойства сборки .NET выше по дереву", "dotnet"),
    ("Directory.Packages.props", "централизованные версии пакетов .NET", "dotnet"),
    ("global.json", "закреплённая версия .NET SDK", "dotnet"),
    ("nuget.config", "источники пакетов NuGet", "dotnet"),
    ("package.json", "пакет Node/npm", "node"),
    ("pnpm-workspace.yaml", "монорепо pnpm", "node"),
    ("lerna.json", "монорепо lerna", "node"),
    ("nx.json", "монорепо nx", "node"),
    ("angular.json", "рабочая область Angular", "node"),
    ("project.json", "проект nx", "node"),
    ("tsconfig.json", "настройка компилятора TypeScript", "node"),
    ("pyproject.toml", "пакет Python (PEP 518)", "python"),
    ("setup.py", "пакет Python (setuptools)", "python"),
    ("setup.cfg", "настройка пакета Python", "python"),
    ("requirements.txt", "зависимости Python", "python"),
    ("Pipfile", "зависимости Python (pipenv)", "python"),
    ("poetry.lock", "зафиксированные зависимости Python", "python"),
    ("uv.lock", "зафиксированные зависимости Python (uv)", "python"),
    ("go.mod", "модуль Go", "go"),
    ("pom.xml", "проект Maven", "jvm"),
    ("build.gradle", "проект Gradle", "jvm"),
    ("build.gradle.kts", "проект Gradle (Kotlin DSL)", "jvm"),
    ("Cargo.toml", "пакет Rust", "rust"),
    ("Gemfile", "зависимости Ruby", "ruby"),
    ("composer.json", "зависимости PHP", "php"),
    ("CMakeLists.txt", "сборка CMake", "native"),
    ("Makefile", "сборка make", "native"),
    ("Dockerfile", "образ контейнера", "deploy"),
    ("docker-compose.yml", "состав контейнеров", "deploy"),
    ("docker-compose.yaml", "состав контейнеров", "deploy"),
    ("Chart.yaml", "чарт Helm", "deploy"),
    (".gitlab-ci.yml", "конвейер GitLab CI", "ci"),
    ("azure-pipelines.yml", "конвейер Azure Pipelines", "ci"),
    ("Jenkinsfile", "конвейер Jenkins", "ci"),
)

# ──────────────────────────────────────────────────────────────────────────────
# Регулярные выражения. Компилируются один раз: на 17 тыс. файлов повторная
# компиляция внутри цикла — заметная часть бюджета.
# ──────────────────────────────────────────────────────────────────────────────

RE_STRING_DQ = re.compile(r'"((?:[^"\\\n]|\\.){2,200})"')
RE_STRING_SQ = re.compile(r"'((?:[^'\\\n]|\\.){2,200})'")
RE_STRING_BQ = re.compile(r"`((?:[^`\\\n]|\\.){2,200})`")

# Литерал-якорь: без пробелов, с буквой или цифрой. Проза отсеивается этим
# условием почти целиком, и отсеивается дёшево — до всякого пересечения множеств.
RE_ANCHORISH = re.compile(r"^[A-Za-z0-9_.:/@$#{}\[\]\-+*?=&,;%~|!]+$")
RE_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")

RE_DECL: dict[str, re.Pattern[str]] = {
    "csharp": re.compile(r"\b(?:class|interface|record|struct|enum|delegate)\s+([A-Za-z_]\w*)"),
    "fsharp": re.compile(r"\b(?:type|module)\s+([A-Za-z_]\w*)"),
    "vbnet": re.compile(r"\b(?:Class|Interface|Structure|Enum|Module)\s+([A-Za-z_]\w*)"),
    "typescript": re.compile(
        r"\b(?:class|interface|enum|type|function|const|let|var|namespace)\s+([A-Za-z_$][\w$]*)"
    ),
    "javascript": re.compile(r"\b(?:class|function|const|let|var)\s+([A-Za-z_$][\w$]*)"),
    "vue": re.compile(r"\b(?:class|function|const|let|var)\s+([A-Za-z_$][\w$]*)"),
    "python": re.compile(r"^[ \t]*(?:class|def)\s+([A-Za-z_]\w*)", re.MULTILINE),
    "java": re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)"),
    "kotlin": re.compile(r"\b(?:class|interface|object|fun)\s+([A-Za-z_]\w*)"),
    "go": re.compile(r"\b(?:type|func)\s+([A-Za-z_]\w*)"),
    "ruby": re.compile(r"\b(?:class|module|def)\s+([A-Za-z_]\w*)"),
    "php": re.compile(r"\b(?:class|interface|trait|function)\s+([A-Za-z_]\w*)"),
    "rust": re.compile(r"\b(?:struct|enum|trait|fn|type)\s+([A-Za-z_]\w*)"),
    "scala": re.compile(r"\b(?:class|trait|object|def)\s+([A-Za-z_]\w*)"),
    "swift": re.compile(r"\b(?:class|struct|enum|protocol|func)\s+([A-Za-z_]\w*)"),
    "cpp": re.compile(r"\b(?:class|struct|enum)\s+([A-Za-z_]\w*)"),
    "c": re.compile(r"\b(?:struct|enum|union)\s+([A-Za-z_]\w*)"),
}

RE_CS_NAMESPACE = re.compile(r"^\s*namespace\s+([A-Za-z_][\w.]*)", re.MULTILINE)
RE_CS_USING = re.compile(
    r"^\s*(?:global\s+)?using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;", re.MULTILINE
)
RE_TS_IMPORT = re.compile(r"""(?:from|import)\s*\(?\s*["']([^"'\n]+)["']""")
RE_TS_REQUIRE = re.compile(r"""require\s*\(\s*["']([^"'\n]+)["']""")
RE_PY_IMPORT = re.compile(
    r"^[ \t]*(?:from\s+([A-Za-z_.][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))", re.MULTILINE
)

RE_FIX_SUBJECT = re.compile(
    r"\b(fix(?:es|ed)?|bug(?:fix)?|hotfix|patch|repair|исправл|фикс|починк|дефект)\b",
    re.IGNORECASE,
)

# Атрибуты и ключи, в которых лежат идентификаторы записей реестра. Список
# намеренно короткий: чем он шире, тем больше конфигурации попадёт в кандидаты,
# а блок 4 обязан быть коротким списком для человека, а не свалкой.
ID_KEYS = frozenset(
    {
        "name",
        "id",
        "key",
        "type",
        "class",
        "ref",
        "code",
        "alias",
        "innername",
        "handler",
        "service",
        "topic",
        "queue",
        "table",
        "command",
        "job",
        "task",
        "route",
        "path",
        "assembly",
        "provider",
        "kind",
    }
)


def _norm_key(name: str) -> str:
    """Имя атрибута без пространства имён XML и в нижнем регистре."""
    return name.rsplit("}", 1)[-1].lower()


# ──────────────────────────────────────────────────────────────────────────────
# git
# ──────────────────────────────────────────────────────────────────────────────


def run_git(root: Path, args: list[str]) -> tuple[int, str]:
    """Позвать git и вернуть код возврата вместе с выводом.

    `core.quotePath=false` — чтобы кириллица в путях приходила как есть,
    а не восьмеричными escape-последовательностями: иначе пути из отчёта
    нельзя скопировать в команду.
    """
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout


def git_available(root: Path) -> bool:
    code, _ = run_git(root, ["rev-parse", "--git-dir"])
    return code == 0


# ──────────────────────────────────────────────────────────────────────────────
# Обход
# ──────────────────────────────────────────────────────────────────────────────


def is_excluded(path: str, extra: Iterable[str]) -> bool:
    parts = PurePosixPath(path).parts
    # Сравнение по СЕГМЕНТАМ, а не глобом: `**/obj/**` не ловит `obj/x.cs`
    # в корне — та самая ловушка `fnmatch`, записанная в CLAUDE.md.
    if any(part in EXCLUDED_SEGMENTS for part in parts[:-1]):
        return True
    name = parts[-1] if parts else path
    if any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return True
    return any(
        pattern in parts or path.startswith(pattern) or name.endswith(pattern) for pattern in extra
    )


def list_files(root: Path, use_git: bool, extra_excludes: list[str]) -> tuple[list[str], list[str]]:
    """Список файлов репозитория и список отсеянных путей.

    Через `git ls-files`, если репозиторий под git: иначе в счётчики попадёт
    вывод сборки, которого в репозитории нет. Порядок — явный `sorted()`,
    никогда не порядок обхода.
    """
    if use_git:
        code, out = run_git(root, ["ls-files", "-z"])
        raw = [p for p in out.split("\0") if p] if code == 0 else []
    else:
        raw = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_SEGMENTS]
            for filename in filenames:
                full = Path(dirpath) / filename
                raw.append(full.relative_to(root).as_posix())

    kept: list[str] = []
    dropped: list[str] = []
    for path in raw:
        if is_excluded(path, extra_excludes):
            dropped.append(path)
        else:
            kept.append(path)
    return sorted(kept), sorted(dropped)


def lang_of(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in BINARY_EXTS:
        return BINARY_LANG
    return LANG_BY_EXT.get(suffix)


# ──────────────────────────────────────────────────────────────────────────────
# Чтение файлов: один проход, всё нужное сразу
# ──────────────────────────────────────────────────────────────────────────────


class FileFacts:
    """Факты об одном файле. `__slots__` не косметика: на 17 тыс. файлов
    словарь атрибутов на объект — заметная часть памяти и времени."""

    __slots__ = (
        "path",
        "lang",
        "size",
        "lines",
        "literals",
        "decls",
        "imports",
        "namespace",
        "ids",
    )

    def __init__(self, path: str, lang: str | None, size: int) -> None:
        self.path = path
        self.lang = lang
        self.size = size
        self.lines = 0
        self.literals: set[str] = set()
        self.decls: set[str] = set()
        self.imports: list[str] = []
        self.namespace: str | None = None
        self.ids: dict[str, set[str]] = {}


def extract_literals(text: str, lang: str) -> set[str]:
    """Строковые литералы, похожие на якорь.

    Отсев идёт до пересечения множеств и по дешёвому признаку: якорь не содержит
    пробелов и содержит хотя бы одну букву или цифру. Проза («Не удалось
    сохранить документ») отсеивается этим целиком, а маршрут, имя очереди
    и имя таблицы — нет.
    """
    found: set[str] = set()
    patterns = [RE_STRING_DQ]
    if lang in {"typescript", "javascript", "vue", "python", "ruby", "php", "shell", "powershell"}:
        patterns.append(RE_STRING_SQ)
    if lang in {"typescript", "javascript", "vue"}:
        patterns.append(RE_STRING_BQ)
    for pattern in patterns:
        for index, match in enumerate(pattern.finditer(text)):
            if index >= MAX_LITERALS_PER_FILE:
                break
            value = match.group(1)
            if len(value) < 3 or len(value) > 120:
                continue
            if not RE_ANCHORISH.match(value) or not RE_HAS_ALNUM.search(value):
                continue
            # `intern` — не микрооптимизация: один и тот же маршрут лежит
            # в сотне файлов, и без склейки строк множества литералов
            # на большом репозитории занимают сотни мегабайт.
            found.add(sys.intern(value))
    return found


def extract_xml_ids(text: str) -> dict[str, set[str]]:
    """Идентификаторы XML по парам «элемент + атрибут».

    Пара, а не просто значение: автору адаптера (R04) нужно знать, из какого
    атрибута какого элемента брать записи, — «в файле есть слово FOO» ему
    не поможет. Заодно пара сама по себе отвечает на «повторяющаяся ли это
    структура»: одна пара с сорока значениями и есть повторяющаяся структура.
    """
    try:
        rootel = ET.fromstring(text)
    except ET.ParseError:
        return {}
    ids: dict[str, set[str]] = defaultdict(set)
    count = 0
    for element in rootel.iter():
        tag = str(element.tag).rsplit("}", 1)[-1]
        for raw_name, value in element.attrib.items():
            key = _norm_key(str(raw_name))
            if key not in ID_KEYS:
                continue
            value = value.strip()
            if len(value) < 3 or len(value) > 200 or " " in value:
                continue
            ids[f"{tag}/@{key}"].add(value)
            count += 1
            if count > MAX_IDS_PER_DATA_FILE:
                return dict(ids)
    return dict(ids)


def _json_walk(node: Any, path: str, ids: dict[str, set[str]], budget: list[int]) -> None:
    if budget[0] <= 0:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            # Ключ, который сам является маршрутом (swagger: `paths./api/x/{id}`),
            # в имя группы не идёт: иначе каждая ручка API даёт собственную
            # группу, и отчёт состоит из сорока одинаковых абзацев.
            segment = "*" if str(key).startswith("/") else str(key)
            if isinstance(value, str) and _norm_key(str(key)) in ID_KEYS:
                value = value.strip()
                if 3 <= len(value) <= 200 and " " not in value:
                    ids[f"{path}.{segment}" if path else segment].add(value)
                    budget[0] -= 1
            else:
                _json_walk(value, f"{path}.{segment}" if path else segment, ids, budget)
    elif isinstance(node, list):
        for item in node:
            _json_walk(item, f"{path}[]", ids, budget)


def extract_json_ids(text: str) -> dict[str, set[str]]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return {}
    ids: dict[str, set[str]] = defaultdict(set)
    _json_walk(data, "", ids, [MAX_IDS_PER_DATA_FILE])
    return dict(ids)


RE_YAML_PAIR = re.compile(r"^[ \t]*-?[ \t]*([A-Za-z_][\w-]*):[ \t]+([^\s#][^\s]*)[ \t]*$")


def extract_yaml_ids(text: str) -> dict[str, set[str]]:
    """YAML разбирается построчно, и это осознанная грубость.

    Разбирателя YAML в стандартной библиотеке нет, а требование «сверх git
    и python ничего не ставить» сильнее полноты: якорные значения в реестрах
    почти всегда записаны простой парой `ключ: значение` в одну строку.
    Ограничение названо вслух в блоке 6, а не спрятано.
    """
    ids: dict[str, set[str]] = defaultdict(set)
    count = 0
    for line in text.splitlines():
        match = RE_YAML_PAIR.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip("'\"")
        if _norm_key(key) not in ID_KEYS:
            continue
        if len(value) < 3 or len(value) > 200:
            continue
        ids[key].add(value)
        count += 1
        if count > MAX_IDS_PER_DATA_FILE:
            break
    return dict(ids)


def scan_files(
    root: Path, files: list[str]
) -> tuple[list[FileFacts], list[str], list[str], list[str]]:
    """Один проход по файлам: строки, литералы, объявления, импорты, идентификаторы.

    Проход именно один. Два прохода по 17 тыс. файлов — это два чтения диска
    и двойной бюджет; всё, что нужно любому блоку отчёта, собирается здесь.
    """
    facts: list[FileFacts] = []
    too_big: list[str] = []
    unreadable: list[str] = []
    minified: list[str] = []
    for path in files:
        full = root / path
        try:
            size = full.stat().st_size
        except OSError:
            unreadable.append(path)
            continue
        lang = lang_of(path)
        item = FileFacts(path, lang, size)
        if lang == BINARY_LANG:
            # Не читается вовсе: строк в двоичном файле нет, литералов тоже,
            # а на abp это 5,5 тыс. файлов и заметная доля времени прогона.
            facts.append(item)
            continue
        if size > MAX_FILE_BYTES:
            too_big.append(path)
            facts.append(item)
            continue
        try:
            raw = full.read_bytes()
        except OSError:
            unreadable.append(path)
            continue
        text = raw.decode("utf-8-sig", errors="replace")
        item.lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)

        # Минифицированный файл — артефакт сборки, а не код, и литералы в нём
        # мерят чужую библиотеку. Признак — длина строки: у бандла она в тысячи
        # символов. Расширение здесь не помогает: `redoc.standalone.js`
        # называется как обычный `.js`, а даёт 1099 «идентификаторов»
        # и первое место в блоке реестров (проверено на squidex).
        #
        # Средней длины строки НЕДОСТАТОЧНО, и это тот же файл показал: 870 КБ
        # на 1819 строк — 478 байт на строку, порог в 500 он не переходит,
        # при том что самая длинная строка в нём 621 848 символов. Поэтому
        # проверяется максимум, а не среднее; для файлов мельче 50 КБ его
        # не считают вовсе — там дороже разбить текст, чем выиграть.
        if lang in CODE_LANGS and item.lines:
            longest = 0
            if size > 50_000:
                longest = max((len(chunk) for chunk in text.split("\n")), default=0)
            if size / item.lines > MAX_AVG_LINE_BYTES or longest > MAX_LINE_CHARS:
                minified.append(path)
                facts.append(item)
                continue

        if lang in CODE_LANGS:
            item.literals = extract_literals(text, lang)
            decl_re = RE_DECL.get(lang)
            if decl_re is not None:
                item.decls = set(decl_re.findall(text))
            if lang == "csharp":
                namespaces = RE_CS_NAMESPACE.findall(text)
                item.namespace = namespaces[0] if namespaces else None
                item.imports = RE_CS_USING.findall(text)
            elif lang in {"typescript", "javascript", "vue"}:
                item.imports = RE_TS_IMPORT.findall(text) + RE_TS_REQUIRE.findall(text)
            elif lang == "python":
                item.imports = [a or b for a, b in RE_PY_IMPORT.findall(text)]
        elif lang == "xml":
            item.ids = extract_xml_ids(text)
        elif lang == "json":
            item.ids = extract_json_ids(text)
        elif lang == "yaml":
            item.ids = extract_yaml_ids(text)
        facts.append(item)
    return facts, too_big, unreadable, minified


# ──────────────────────────────────────────────────────────────────────────────
# Блок 1. Чем собран репозиторий
# ──────────────────────────────────────────────────────────────────────────────


def block_composition(facts: list[FileFacts], files: list[str]) -> dict[str, Any]:
    by_lang: dict[str, list[FileFacts]] = defaultdict(list)
    for item in facts:
        by_lang[item.lang or "прочее"].append(item)

    total_lines = sum(item.lines for item in facts) or 1
    languages = sorted(
        (
            {
                "language": lang,
                "files": len(items),
                "lines": sum(item.lines for item in items),
                "share_lines": round(sum(item.lines for item in items) / total_lines, 4),
            }
            for lang, items in by_lang.items()
        ),
        key=lambda row: (-int(row["lines"]), str(row["language"])),
    )

    names = {path: PurePosixPath(path).name for path in files}
    build: list[dict[str, Any]] = []
    stacks: set[str] = set()
    for pattern, means, stack in BUILD_FILES:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            hits = [path for path, name in names.items() if name.endswith(suffix)]
        else:
            lowered = pattern.lower()
            hits = [path for path, name in names.items() if name.lower() == lowered]
        if not hits:
            continue
        hits.sort()
        stacks.add(stack)
        build.append(
            {
                "pattern": pattern,
                "means": means,
                "stack": stack,
                "count": len(hits),
                "examples": hits[:5],
            }
        )

    layout: list[dict[str, Any]] = []
    per_dir: dict[str, list[FileFacts]] = defaultdict(list)
    for item in facts:
        parts = PurePosixPath(item.path).parts
        per_dir[parts[0] if len(parts) > 1 else "."].append(item)
    for name, items in per_dir.items():
        langs = Counter(item.lang for item in items if item.lang in CODE_LANGS)
        layout.append(
            {
                "dir": name,
                "files": len(items),
                "lines": sum(item.lines for item in items),
                "main_language": langs.most_common(1)[0][0] if langs else "—",
            }
        )
    layout.sort(key=lambda row: (-int(row["files"]), str(row["dir"])))

    # «Прочее» на большом репозитории бывает больше всех языков вместе взятых
    # (на abp — 68 % строк), и безымянное «прочее» в отчёте о незнакомом
    # репозитории бесполезно: первый вопрос к нему — «а что это».
    unknown: Counter[str] = Counter()
    unknown_lines: Counter[str] = Counter()
    for item in facts:
        if item.lang is not None:
            continue
        suffix = PurePosixPath(item.path).suffix.lower() or "(без расширения)"
        unknown[suffix] += 1
        unknown_lines[suffix] += item.lines

    return {
        "files_total": len(files),
        "lines_total": sum(item.lines for item in facts),
        "languages": languages,
        "build_files": build,
        "stacks": sorted(stacks),
        "layout": layout[:20],
        "unknown_extensions": [
            {"extension": suffix, "files": count, "lines": unknown_lines[suffix]}
            for suffix, count in sorted(
                unknown.items(), key=lambda kv: (-unknown_lines[kv[0]], kv[0])
            )[:10]
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Блок 2. Что читать первым
# ──────────────────────────────────────────────────────────────────────────────


def head_info(root: Path) -> dict[str, str] | None:
    code, out = run_git(root, ["log", "-1", "--format=%H%x02%cI"])
    if code != 0 or not out.strip():
        return None
    commit, _, date = out.strip().partition("\x02")
    return {"commit": commit, "date": date}


def block_archaeology(
    root: Path, facts: list[FileFacts], head: dict[str, str] | None, months: int, top: int
) -> dict[str, Any]:
    """Археология по git: кто и как часто трогал файлы.

    Окно отсчитывается от даты коммита HEAD, а не от `datetime.now()`. Это
    единственный способ получить одинаковый ответ на одном и том же репозитории
    в разные дни — то есть выполнить требование детерминизма, не отказываясь
    от вопроса «что менялось недавно».
    """
    if head is None:
        return {
            "available": False,
            "reason": "репозиторий не под git — археология недоступна, а выдумывать её нечем",
        }

    args = ["log", "--format=\x01%H\x02%an\x02%cI\x02%s", "--name-only", "--no-renames"]
    since = None
    if months > 0:
        head_date = datetime.fromisoformat(head["date"])
        since = (head_date - timedelta(days=int(months * 30.44))).date().isoformat()
        args.insert(1, f"--since={since}")
    code, out = run_git(root, args)
    if code != 0:
        return {"available": False, "reason": "git log завершился с ошибкой"}

    known = {item.path: item for item in facts}
    churn: Counter[str] = Counter()
    fixes: Counter[str] = Counter()
    authors: dict[str, Counter[str]] = defaultdict(Counter)
    commits = 0
    current_author = ""
    current_is_fix = False
    for line in out.splitlines():
        if line.startswith("\x01"):
            commits += 1
            parts = line[1:].split("\x02")
            current_author = parts[1] if len(parts) > 1 else ""
            current_is_fix = bool(RE_FIX_SUBJECT.search(parts[3])) if len(parts) > 3 else False
            continue
        path = line.strip()
        if not path or path not in known:
            continue
        churn[path] += 1
        authors[path][current_author] += 1
        if current_is_fix:
            fixes[path] += 1

    hotspots = sorted(
        (
            {
                "path": path,
                "commits": count,
                "lines": known[path].lines,
                "score": count * known[path].lines,
            }
            for path, count in churn.items()
            if known[path].lang in CODE_LANGS
        ),
        key=lambda row: (-int(row["score"]), str(row["path"])),
    )[:top]

    # Bus factor считается по каталогу, а не по файлу: «этот файл писал один
    # человек» — норма, «этот каталог писал один человек» — риск.
    dir_authors: dict[str, Counter[str]] = defaultdict(Counter)
    for path, counter in authors.items():
        parts = PurePosixPath(path).parts
        directory = "/".join(parts[: min(2, max(len(parts) - 1, 1))])
        dir_authors[directory].update(counter)
    bus: list[dict[str, Any]] = []
    for directory, counter in dir_authors.items():
        total = sum(counter.values())
        if total < BUS_FACTOR_MIN_COMMITS:
            continue
        author, count = counter.most_common(1)[0]
        share = count / total
        if share >= BUS_FACTOR_SHARE:
            bus.append(
                {
                    "dir": directory,
                    "author": author,
                    "share": round(share, 3),
                    "touches": total,
                    "authors": len(counter),
                }
            )
    bus.sort(key=lambda row: (-int(row["touches"]), str(row["dir"])))

    untouched = sorted(
        (item for item in facts if item.lang in CODE_LANGS and item.path not in churn),
        key=lambda item: (-item.lines, item.path),
    )
    code_files = [item for item in facts if item.lang in CODE_LANGS]

    return {
        "available": True,
        "window_months": months,
        "since": since,
        "head": head,
        "commits_in_window": commits,
        "hotspots": hotspots,
        "most_changed": [
            {"path": path, "commits": count}
            for path, count in sorted(churn.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        ],
        "most_fixed": [
            {"path": path, "fix_commits": count}
            for path, count in sorted(fixes.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        ],
        "bus_factor_one": bus[:top],
        "untouched": {
            "count": len(untouched),
            "of_code_files": len(code_files),
            "largest": [{"path": item.path, "lines": item.lines} for item in untouched[:top]],
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Блок 3. Где центр системы
# ──────────────────────────────────────────────────────────────────────────────


def build_import_graph(facts: list[FileFacts]) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Граф импортов по строчным признакам — и только по ним.

    Семантики здесь нет и не будет: `using` и `import` ловятся построчно,
    и этого достаточно для вопроса «где центр». Всё, что глубже (кто кем
    реально пользуется), — работа движка разбора и графа, то есть другой
    этап и другой бюджет.
    """
    by_path = {item.path: item for item in facts}
    known = set(by_path)
    edges: dict[str, set[str]] = defaultdict(set)
    stats: Counter[str] = Counter()

    ns_files: dict[str, list[str]] = defaultdict(list)
    for item in facts:
        if item.lang == "csharp" and item.namespace:
            ns_files[item.namespace].append(item.path)

    py_modules: dict[str, str] = {}
    for item in facts:
        if item.lang != "python":
            continue
        parts = list(PurePosixPath(item.path).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):
            py_modules.setdefault(".".join(parts[start:]), item.path)

    for item in facts:
        if not item.imports:
            continue
        for spec in item.imports:
            stats["imports_total"] += 1
            target: str | None = None
            if item.lang == "csharp":
                targets = ns_files.get(spec, [])
                if targets and len(targets) <= NAMESPACE_FANOUT_CAP:
                    for candidate in targets:
                        if candidate != item.path:
                            edges[item.path].add(candidate)
                    stats["resolved"] += 1
                    continue
                stats["fanout_skipped" if targets else "unresolved"] += 1
                continue
            if item.lang in {"typescript", "javascript", "vue"}:
                if not spec.startswith("."):
                    stats["external"] += 1
                    continue
                base = (PurePosixPath(item.path).parent / spec).as_posix()
                base = os.path.normpath(base).replace(os.sep, "/")
                for suffix in ("", ".ts", ".tsx", ".d.ts", ".js", ".jsx", "/index.ts", "/index.js"):
                    candidate = f"{base}{suffix}"
                    if candidate in known:
                        target = candidate
                        break
            elif item.lang == "python":
                module = spec
                if module.startswith("."):
                    package = PurePosixPath(item.path).parent
                    stripped = module.lstrip(".")
                    up = len(module) - len(stripped) - 1
                    for _ in range(up):
                        package = package.parent
                    module = ".".join(filter(None, [*package.parts, *stripped.split(".")]))
                target = py_modules.get(module)
                if target is None and "." in module:
                    target = py_modules.get(module.rsplit(".", 1)[0])
            if target and target != item.path:
                edges[item.path].add(target)
                stats["resolved"] += 1
            else:
                stats["external" if item.lang != "python" else "unresolved"] += 1
    return edges, dict(stats)


def pagerank(nodes: list[str], edges: dict[str, set[str]]) -> dict[str, float]:
    """PageRank по графу импортов. Двадцать итераций, затухание 0.85.

    Итераций фиксированное число, а не «до сходимости»: сходимость по epsilon
    зависит от порядка обхода словаря и делает результат невоспроизводимым
    между запусками на разных версиях Python.
    """
    index = {node: position for position, node in enumerate(nodes)}
    count = len(nodes)
    if count == 0:
        return {}
    outgoing: list[list[int]] = [[] for _ in range(count)]
    for source, targets in edges.items():
        if source not in index:
            continue
        outgoing[index[source]] = sorted(index[target] for target in targets if target in index)
    rank = [1.0 / count] * count
    damping = 0.85
    for _ in range(PAGERANK_ITERATIONS):
        nxt = [(1.0 - damping) / count] * count
        dangling = 0.0
        for position, targets in enumerate(outgoing):
            if not targets:
                dangling += rank[position]
                continue
            share = damping * rank[position] / len(targets)
            for target in targets:
                nxt[target] += share
        if dangling:
            spread = damping * dangling / count
            nxt = [value + spread for value in nxt]
        rank = nxt
    return {node: rank[index[node]] for node in nodes}


def block_structure(facts: list[FileFacts], top: int) -> dict[str, Any]:
    graph_langs = {"csharp", "typescript", "javascript", "vue", "python"}
    nodes = sorted(item.path for item in facts if item.lang in graph_langs)
    edges, stats = build_import_graph(facts)
    ranks = pagerank(nodes, edges)
    incoming: Counter[str] = Counter()
    for targets in edges.values():
        for target in targets:
            incoming[target] += 1

    by_path = {item.path: item for item in facts}
    center = sorted(
        (
            {
                "path": path,
                "pagerank": round(value * len(nodes), 4),
                "incoming": incoming.get(path, 0),
                "lines": by_path[path].lines,
            }
            for path, value in ranks.items()
        ),
        key=lambda row: (-float(row["pagerank"]), str(row["path"])),
    )[:top]

    entryish = re.compile(
        r"(^|/)(main|program|index|__init__|__main__|setup|conftest|app|startup)\b", re.IGNORECASE
    )
    testish = re.compile(r"(^|/)(tests?|specs?|__tests__)(/|$)|\.(test|spec)\.", re.IGNORECASE)
    orphans = [
        path
        for path in nodes
        if incoming.get(path, 0) == 0
        and not entryish.search(path)
        and not testish.search(path)
        and by_path[path].lines > 20
    ]
    # Вес по каталогу, а не только по файлу. На C# импорт ловится в пространство
    # имён, а не в файл, поэтому все файлы одного пространства получают
    # одинаковый вес: на abp первые четыре строки списка — 135.456 у каждой.
    # По каталогам такой ничьей не бывает, и ответ на «где центр» читается
    # с первого взгляда.
    per_dir: Counter[str] = Counter()
    per_dir_files: Counter[str] = Counter()
    for path, value in ranks.items():
        directory = PurePosixPath(path).parent.as_posix() or "."
        per_dir[directory] += value * len(nodes)
        per_dir_files[directory] += 1
    center_dirs = [
        {"dir": directory, "pagerank": round(value, 3), "files": per_dir_files[directory]}
        for directory, value in sorted(per_dir.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    ]

    return {
        "nodes": len(nodes),
        "edges": sum(len(targets) for targets in edges.values()),
        "import_stats": {key: stats.get(key, 0) for key in sorted(stats)},
        "center": center,
        "center_dirs": center_dirs,
        "note": (
            "на C# импорт ловится в ПРОСТРАНСТВО ИМЁН, а не в файл: все файлы "
            "одного пространства получают одинаковый вес и стоят в списке рядом. "
            "Это не ошибка счёта, а гранулярность источника"
        ),
        "no_incoming_imports": {
            "count": len(orphans),
            "note": (
                "кандидаты в мёртвые зоны БЕЗ СЕМАНТИКИ: считается только то, что "
                "видно построчно. Рефлексия, DI-регистрация, сборка имени из кусков "
                "и шаблоны Angular сюда не попадают — файл из этого списка может "
                "быть живым"
            ),
            "examples": sorted(orphans, key=lambda path: (-by_path[path].lines, path))[:top],
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Блоки 4 и 5. Чем система заякорена и как языки говорят между собой
# ──────────────────────────────────────────────────────────────────────────────

LITERAL_STOPLIST = frozenset(
    {
        "true",
        "false",
        "null",
        "none",
        "utf-8",
        "utf8",
        "application/json",
        "text/plain",
        "text/html",
        "content-type",
        "authorization",
        "accept",
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "http",
        "https",
        "localhost",
        "0.0.0.0",
        "127.0.0.1",
    }
)
RE_VERSIONISH = re.compile(r"^[vV]?\d+(\.\d+)*([-+][\w.]+)?$")
RE_TABLEISH = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")
RE_DOTTEDISH = re.compile(r"^[A-Za-z_]\w*(\.\w+)+$")
RE_KEBABISH = re.compile(r"^[a-z0-9]+([-.][a-z0-9]+)+$")


def literal_is_noise(value: str) -> bool:
    lowered = value.lower()
    if lowered in LITERAL_STOPLIST:
        return True
    if RE_VERSIONISH.match(value):
        return True
    suffix = PurePosixPath(value).suffix.lower()
    return bool(suffix) and (suffix in LANG_BY_EXT or suffix in BINARY_EXTS)


def seam_kind(value: str) -> str:
    # Косая черта — достаточный признак: маршрут пишут и как `/api/x`,
    # и как `api/x`, и как `x/y/z`, а имена файлов до этой проверки
    # уже отсеяны как шум по расширению.
    if "/" in value:
        return "маршрут"
    if RE_TABLEISH.match(value):
        return "имя в верхнем регистре (таблица, тип события, код)"
    if RE_DOTTEDISH.match(value):
        return "составное имя (тип, ключ настройки, топик)"
    if RE_KEBABISH.match(value):
        return "имя через дефис (очередь, сервис, ключ)"
    return "идентификатор"


def build_declaration_index(facts: list[FileFacts]) -> dict[str, list[str]]:
    """Имя объявления → где оно объявлено (не более трёх файлов на имя).

    Нужен блоку 4: запись реестра, совпавшая с именем класса, полезна только
    вместе с ответом «каким именно классом». Без него отчёт говорит «такое имя
    в коде объявлено» и оставляет искать руками.
    """
    index: dict[str, list[str]] = defaultdict(list)
    for item in facts:
        for name in item.decls:
            where = index[name]
            if len(where) < 3:
                where.append(item.path)
    return {name: sorted(where) for name, where in index.items()}


def build_literal_index(
    facts: list[FileFacts],
) -> tuple[dict[str, int], dict[str, set[str]], dict[str, list[str]]]:
    """Литерал → сколько файлов, каких языков и примеры путей.

    Примеров не больше десяти на литерал: в отчёт идут примеры, а не полный
    список вхождений, а память на большом репозитории кончается именно здесь.
    """
    counts: Counter[str] = Counter()
    langs: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, list[str]] = defaultdict(list)
    for item in facts:
        if item.lang not in CODE_LANGS:
            continue
        for value in item.literals:
            counts[value] += 1
            langs[value].add(item.lang)
            if len(examples[value]) < 10:
                examples[value].append(item.path)
    return dict(counts), dict(langs), {key: sorted(value) for key, value in examples.items()}


def block_registries(
    facts: list[FileFacts],
    counts: dict[str, int],
    examples: dict[str, list[str]],
    declarations: dict[str, list[str]],
    top: int,
) -> dict[str, Any]:
    """Кандидаты в реестры: файл управляет кодом, а код о нём не знает по имени.

    Признак — пересечение, а не форма файла. Отличить реестр от конфигурации
    по расширению нельзя: и то и другое `.xml`. Проверяемое отличие: множество
    идентификаторов файла данных пересекается с множеством строковых литералов
    кода.

    Дальше кандидаты делятся на два вида, и разница между ними — это разница
    в цене адаптера (R04):

    * **реестр** — идентификаторов с такими именами в коде НЕ объявлено. Связь
      «запись → класс» не выводится ниоткуда, кроме самого реестра, и делать
      её обязаны мы. Это трудный случай, ради которого затевалась разведка;
    * **реестр по именам типов** — записи названы именами классов. Связь
      восстанавливается сопоставлением имён, адаптер дешевле на порядок.
      Случай проверен на wexflow: `Task/@name` в 756 файлах workflow — это
      имена классов задач.

    Считается по КАТАЛОГУ, а не по файлу. Реестром часто является не файл,
    а каталог из сотен однотипных файлов (wexflow: один workflow — один XML),
    и пофайловый счёт даёт либо 756 строк отчёта, либо ноль: у отдельного файла
    идентификаторов слишком мало, чтобы перейти любой порог.
    """
    # (каталог, группа) → значения и файлы. Группа — пара «элемент + атрибут»
    # для XML, путь ключа для JSON, имя ключа для YAML.
    grouped: dict[tuple[str, str], tuple[set[str], list[str]]] = {}
    for item in facts:
        if not item.ids:
            continue
        directory = PurePosixPath(item.path).parent.as_posix() or "."
        for group, values in item.ids.items():
            key = (directory, group)
            bucket = grouped.setdefault(key, (set(), []))
            if len(bucket[0]) < MAX_IDS_PER_DATA_FILE:
                bucket[0].update(values)
            bucket[1].append(item.path)

    candidates: list[dict[str, Any]] = []
    for (directory, group), (values, paths) in grouped.items():
        # Два независимых признака совпадения, и путать их нельзя.
        # Литерал в коде — «код знает эту строку». Объявление с таким именем
        # — «в коде есть класс с этим именем». Второе на wexflow есть, а первого
        # нет вовсе: имя задачи в XML совпадает с именем класса и НИ РАЗУ
        # не встречается строкой. Детектор, требующий литерала, объявил бы,
        # что реестров в wexflow нет, — при 756 файлах workflow.
        as_literal = sorted(value for value in values if counts.get(value, 0) > 0)
        as_declaration = sorted(value for value in values if value in declarations)
        undeclared = [value for value in as_literal if value not in declarations]
        if len(undeclared) >= 3:
            kind, shown = "реестр", undeclared
        elif len(as_declaration) >= 3:
            kind, shown = "реестр по именам типов", as_declaration
        else:
            continue
        matched = sorted(set(as_literal) | set(as_declaration))
        paths = sorted(paths)
        candidates.append(
            {
                "_key": (group, tuple(matched)),
                "path": paths[0] if len(paths) == 1 else f"{directory}/* ({len(paths)} файлов)",
                "files": len(paths),
                "kind": kind,
                "group": group,
                "ids": len(values),
                "matched_in_code": len(matched),
                "declared_in_code": len(as_declaration),
                "score": round(len(matched) / max(len(values), 1), 3),
                "examples": [
                    {
                        "id": value,
                        "used_in": examples.get(value, [])[:3],
                        "declared_in": declarations.get(value, [])[:2],
                    }
                    for value in shown[:5]
                ],
            }
        )

    # Реестр бывает исполняемым кодом, а не файлом данных — случай, ради
    # которого в плане заведён отдельный вид адаптера. Признак тот же —
    # пересечение, — но с добавкой,
    # без которой в кандидаты попадает любой крупный сервис: реестр состоит
    # ИЗ ДАННЫХ, поэтому литералов у него примерно по одному на строку.
    # Проверено на wexflow: без этого условия первые три места занимали
    # `WexflowService.cs` (235 литералов на 2000 строк) и минифицированный
    # `designer.js` — файлы, реестром не являющиеся ничем.
    for item in facts:
        if item.lang not in CODE_LANGS or len(item.literals) < 5 or item.lines < 5:
            continue
        if len(item.literals) / item.lines < 0.5:
            continue
        cross: list[str] = []
        for value in sorted(item.literals):
            if value in declarations or literal_is_noise(value):
                continue
            others = [path for path in examples.get(value, []) if path != item.path]
            if others and any(lang_of(path) != item.lang for path in others):
                cross.append(value)
        if len(cross) < 3:
            continue
        candidates.append(
            {
                "path": item.path,
                "files": 1,
                "kind": "реестр в виде кода",
                "group": "строковые литералы файла",
                "ids": len(item.literals),
                "matched_in_code": len(cross),
                "declared_in_code": 0,
                "score": round(len(cross) / max(len(item.literals), 1), 3),
                "examples": [
                    {
                        "id": value,
                        "used_in": [p for p in examples.get(value, []) if p != item.path][:3],
                        "declared_in": [],
                    }
                    for value in cross[:5]
                ],
            }
        )

    candidates.sort(
        key=lambda row: (-int(row["matched_in_code"]), -float(row["score"]), str(row["path"]))
    )

    # Одна и та же группа с тем же набором значений в нескольких каталогах —
    # это копии одного реестра (wexflow держит четыре: samples/net,
    # samples/netcore/{windows,linux,macos}). Четыре одинаковых абзаца вытесняют
    # из отчёта то, чего человек ещё не видел, поэтому копии сворачиваются
    # в строку «то же самое лежит ещё здесь».
    unique: list[dict[str, Any]] = []
    kept_by_group: dict[str, list[tuple[set[str], dict[str, Any]]]] = defaultdict(list)
    for row in candidates:
        key = row.pop("_key", None)
        if key is None:
            unique.append(row)
            continue
        group, values = key
        current = set(values)
        copy_of = None
        for earlier, earlier_row in kept_by_group[group]:
            # Пересечение, а не равенство: копии реестра расходятся на пару
            # записей (у wexflow — 104, 103 и 98 значений в одной группе),
            # и точное сравнение множеств их уже не сводит.
            if len(current & earlier) >= 0.8 * len(current):
                copy_of = earlier_row
                break
        if copy_of is not None:
            copy_of.setdefault("also_in", []).append(str(row["path"]))
            continue
        kept_by_group[group].append((current, row))
        unique.append(row)
    candidates = unique

    return {
        "found": len(candidates),
        "note": (
            "это КАНДИДАТЫ, а не реестры. Признак — пересечение идентификаторов "
            "файла с литералами кода; он ловит и файлы, которые реестром "
            "не являются. Подтверждает человек (Р10)"
        ),
        "candidates": candidates[:top],
    }


def block_seams(
    counts: dict[str, int],
    langs: dict[str, set[str]],
    examples: dict[str, list[str]],
    declared: set[str],
    top: int,
) -> dict[str, Any]:
    """Швы: литерал, который знают по обе стороны языковой границы.

    Между языками вызовов нет — есть сообщение по строке, которую обе стороны
    обязаны знать (правило Р2 плана). Поэтому шов ищется ровно так: один
    и тот же литерал в файлах разных языков.
    """
    rows: list[dict[str, Any]] = []
    for value, value_langs in langs.items():
        if len(value_langs) < 2 or literal_is_noise(value):
            continue
        rows.append(
            {
                "literal": value,
                "kind": seam_kind(value),
                "languages": sorted(value_langs),
                "files": counts.get(value, 0),
                "declared_in_code": value in declared,
                "examples": examples.get(value, [])[:4],
            }
        )
    rows.sort(
        key=lambda row: (
            -len(list(row["languages"])),
            -int(row["files"]),
            str(row["literal"]),
        )
    )
    by_kind: Counter[str] = Counter(str(row["kind"]) for row in rows)
    by_pair: Counter[str] = Counter(
        " ↔ ".join(sorted(row["languages"])) for row in rows if len(list(row["languages"])) == 2
    )
    return {
        "found": len(rows),
        "by_kind": [{"kind": kind, "count": count} for kind, count in sorted(by_kind.items())],
        "by_language_pair": [
            {"pair": pair, "count": count}
            for pair, count in sorted(by_pair.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "candidates": rows[:top],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Блок 6. Чему в этом отчёте нельзя верить
# ──────────────────────────────────────────────────────────────────────────────


def block_limits(
    dropped: list[str],
    too_big: list[str],
    unreadable: list[str],
    minified: list[str],
    top: int,
) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    for path in dropped:
        parts = PurePosixPath(path).parts
        segment = next((part for part in parts if part in EXCLUDED_SEGMENTS), None)
        if segment:
            reasons[f"каталог {segment}/"] += 1
            continue
        suffix = next((s for s in EXCLUDED_SUFFIXES if path.endswith(s)), None)
        reasons[f"суффикс {suffix}" if suffix else "правило пользователя"] += 1
    return {
        "excluded_files": len(dropped),
        "excluded_by_reason": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "too_big": {
            "limit_bytes": MAX_FILE_BYTES,
            "count": len(too_big),
            "examples": too_big[:top],
        },
        "unreadable": {"count": len(unreadable), "examples": unreadable[:top]},
        "minified": {
            "limit_avg_line_bytes": MAX_AVG_LINE_BYTES,
            "limit_line_chars": MAX_LINE_CHARS,
            "count": len(minified),
            "examples": minified[:top],
        },
        "known_gaps": [
            "YAML разбирается построчно: реестр, у которого значения записаны "
            "блочными списками или многострочно, в блок 4 не попадёт",
            "импорты ловятся построчно: рефлексия, DI-регистрация и сборка имени "
            "из кусков графу импортов не видны, поэтому «мёртвые зоны» — кандидаты",
            "литералы берутся из одинарных, двойных и обратных кавычек одной "
            "строкой; строка, собранная конкатенацией или интерполяцией, не шов "
            "и не идентификатор для этого отчёта",
            "разведка ничего не знает о семантике: совпадение литералов — повод "
            "посмотреть, а не факт связи",
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Сборка отчёта
# ──────────────────────────────────────────────────────────────────────────────

QUESTIONS: tuple[tuple[str, str], ...] = (
    ("composition", "Чем собран репозиторий?"),
    ("archaeology", "Что читать первым?"),
    ("structure", "Где центр системы?"),
    ("registries", "Чем система заякорена?"),
    ("seams", "Как языки говорят между собой?"),
    ("limits", "Чему в этом отчёте нельзя верить?"),
)


def build_report(root: Path, months: int, top: int, extra_excludes: list[str]) -> dict[str, Any]:
    use_git = git_available(root)
    files, dropped = list_files(root, use_git, extra_excludes)
    facts, too_big, unreadable, minified = scan_files(root, files)
    head = head_info(root) if use_git else None

    counts, langs, examples = build_literal_index(facts)
    declarations = build_declaration_index(facts)
    declared = set(declarations)

    data = {
        "composition": block_composition(facts, files),
        "archaeology": block_archaeology(root, facts, head, months, top),
        "structure": block_structure(facts, top),
        "registries": block_registries(facts, counts, examples, declarations, top),
        "seams": block_seams(counts, langs, examples, declared, top),
        "limits": block_limits(dropped, too_big, unreadable, minified, top),
    }
    return {
        "schema": SCHEMA,
        # Абсолютного пути здесь нет намеренно: он меняется от машины к машине,
        # и два одинаковых репозитория дали бы разные отчёты. Имя каталога
        # достаточно, чтобы понять, о чём отчёт.
        "repo": root.resolve().name,
        "vcs": "git" if use_git else "нет",
        "head": head,
        "params": {"months": months, "top": top, "excludes": sorted(extra_excludes)},
        "blocks": [
            {"id": block_id, "question": question, "data": data[block_id]}
            for block_id, question in QUESTIONS
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Человеческая форма
# ──────────────────────────────────────────────────────────────────────────────


def _section(title: str) -> str:
    return f"\n\n========== {title} ==========\n"


def _rows(rows: list[dict[str, Any]], columns: list[tuple[str, str]], empty: str) -> list[str]:
    """Таблица или названная вслух пустота.

    Пустой список печатается строкой «ничего не найдено», а не пропуском:
    пропуск в отчёте неотличим от «блок не отработал» — это правило Р7 плана,
    перенесённое на разведку.
    """
    if not rows:
        return [f"  {empty}"]
    widths = [
        max(len(title), *(len(str(row.get(key, ""))) for row in rows)) for key, title in columns
    ]
    out = [
        "  "
        + "  ".join(title.ljust(width) for (_, title), width in zip(columns, widths, strict=True))
    ]
    out.append("  " + "  ".join("─" * width for width in widths))
    for row in rows:
        out.append(
            "  "
            + "  ".join(
                str(row.get(key, "")).ljust(width)
                for (key, _), width in zip(columns, widths, strict=True)
            )
        )
    return out


def render_text(report: dict[str, Any]) -> str:
    blocks = {block["id"]: block for block in report["blocks"]}
    lines: list[str] = []
    head = report.get("head")
    lines.append(f"РАЗВЕДКА РЕПОЗИТОРИЯ: {report['repo']}")
    lines.append(f"версия схемы: {report['schema']}; контроль версий: {report['vcs']}")
    if head:
        lines.append(f"HEAD: {head['commit'][:12]} от {head['date']}")
    lines.append(
        "Окно археологии отсчитано от даты HEAD, а не от сегодняшнего дня: "
        "иначе один и тот же репозиторий давал бы разные ответы в разные дни."
    )

    composition = blocks["composition"]["data"]
    lines.append(_section(f"1. {blocks['composition']['question']}"))
    lines.append(
        f"  файлов: {composition['files_total']}, строк: {composition['lines_total']}, "
        f"стеки: {', '.join(composition['stacks']) or 'не опознаны'}"
    )
    lines.append("")
    lines.append("  Языки:")
    lines.extend(
        _rows(
            composition["languages"][:15],
            [
                ("language", "язык"),
                ("files", "файлов"),
                ("lines", "строк"),
                ("share_lines", "доля"),
            ],
            "языков не опознано",
        )
    )
    lines.append("")
    lines.append("  Чем собрано (файл сборки → что из него следует):")
    lines.extend(
        _rows(
            composition["build_files"],
            [("pattern", "файл"), ("count", "шт"), ("means", "что это значит")],
            "файлов сборки не найдено — репозиторий не собирается ничем известным",
        )
    )
    lines.append("")
    lines.append("  Не опознано по расширению (чем это может быть — вопрос к человеку):")
    lines.extend(
        _rows(
            composition["unknown_extensions"][:8],
            [("extension", "расширение"), ("files", "файлов"), ("lines", "строк")],
            "опознано всё",
        )
    )
    lines.append("")
    lines.append("  Раскладка верхнего уровня:")
    lines.extend(
        _rows(
            composition["layout"][:12],
            [
                ("dir", "каталог"),
                ("files", "файлов"),
                ("lines", "строк"),
                ("main_language", "язык"),
            ],
            "раскладки нет: всё в корне",
        )
    )

    archaeology = blocks["archaeology"]["data"]
    lines.append(_section(f"2. {blocks['archaeology']['question']}"))
    if not archaeology.get("available"):
        lines.append(f"  {archaeology['reason']}")
    else:
        lines.append(
            f"  окно: {archaeology['window_months']} мес."
            f"{' с ' + archaeology['since'] if archaeology['since'] else ' (вся история)'}, "
            f"коммитов в окне: {archaeology['commits_in_window']}"
        )
        lines.append("")
        lines.append("  Хотспоты (правки × размер — что меняют часто и что при этом велико):")
        lines.extend(
            _rows(
                archaeology["hotspots"],
                [("path", "файл"), ("commits", "правок"), ("lines", "строк"), ("score", "вес")],
                "правок в окне нет",
            )
        )
        lines.append("")
        lines.append("  Что меняют чаще всего (число коммитов, без поправки на размер):")
        lines.extend(
            _rows(
                archaeology["most_changed"],
                [("path", "файл"), ("commits", "правок")],
                "правок в окне нет",
            )
        )
        lines.append("")
        lines.append("  Что чинят чаще всего (коммиты со словами про исправление):")
        lines.extend(
            _rows(
                archaeology["most_fixed"],
                [("path", "файл"), ("fix_commits", "правок-фиксов")],
                "коммитов про исправления в окне нет",
            )
        )
        lines.append("")
        lines.append("  Каталоги одного автора (bus factor = 1):")
        lines.extend(
            _rows(
                archaeology["bus_factor_one"],
                [
                    ("dir", "каталог"),
                    ("author", "автор"),
                    ("share", "доля"),
                    ("touches", "касаний"),
                ],
                "каталогов с единственным автором нет",
            )
        )
        untouched = archaeology["untouched"]
        lines.append("")
        lines.append(
            f"  Не менялось за окно: {untouched['count']} из {untouched['of_code_files']} "
            f"файлов кода. Самые крупные:"
        )
        lines.extend(
            _rows(untouched["largest"], [("path", "файл"), ("lines", "строк")], "таких нет")
        )

    structure = blocks["structure"]["data"]
    lines.append(_section(f"3. {blocks['structure']['question']}"))
    lines.append(
        f"  узлов графа импортов: {structure['nodes']}, рёбер: {structure['edges']}, "
        f"импортов разобрано: {structure['import_stats'].get('resolved', 0)} "
        f"из {structure['import_stats'].get('imports_total', 0)}"
    )
    lines.append("")
    lines.append("  Центр по PageRank (кого импортируют те, кого импортируют):")
    lines.extend(
        _rows(
            structure["center"],
            [("path", "файл"), ("pagerank", "вес"), ("incoming", "входящих"), ("lines", "строк")],
            "граф импортов пуст: в этом репозитории импорты построчно не ловятся",
        )
    )
    lines.append("")
    lines.append("  Центр по каталогам (сумма веса файлов каталога):")
    lines.extend(
        _rows(
            structure["center_dirs"],
            [("dir", "каталог"), ("pagerank", "вес"), ("files", "файлов")],
            "каталогов нет",
        )
    )
    lines.append(f"  {structure['note']}.")
    orphans = structure["no_incoming_imports"]
    lines.append("")
    lines.append(f"  Никем не импортируется: {orphans['count']} файлов. {orphans['note']}.")
    lines.extend(
        _rows([{"path": path} for path in orphans["examples"]], [("path", "файл")], "таких нет")
    )

    registries = blocks["registries"]["data"]
    lines.append(_section(f"4. {blocks['registries']['question']}"))
    lines.append(f"  {registries['note']}.")
    lines.append("")
    if not registries["candidates"]:
        lines.append(
            "  Кандидатов в реестры нет. Это законный ответ: на большинстве "
            "репозиториев точки входа объявлены в коде, и реестр не нужен."
        )
    else:
        for candidate in registries["candidates"]:
            lines.append(
                f"  {candidate['path']}  [{candidate['kind']}]  {candidate['group']}: "
                f"идентификаторов {candidate['ids']}, совпало с кодом "
                f"{candidate['matched_in_code']}"
                + (
                    f" (объявлений с такими именами: {candidate['declared_in_code']})"
                    if candidate["declared_in_code"]
                    else ""
                )
            )
            for example in candidate["examples"]:
                where = ", ".join(example["used_in"])
                if example.get("declared_in"):
                    declared_at = ", ".join(example["declared_in"])
                    where = (
                        f"{where}; объявлено: {declared_at}"
                        if where
                        else f"объявлено: {declared_at}"
                    )
                lines.append(f"      {example['id']}  →  {where or '—'}")
            if candidate.get("also_in"):
                lines.append(
                    f"      то же самое лежит ещё в: {', '.join(candidate['also_in'][:4])}"
                )

    seams = blocks["seams"]["data"]
    lines.append(_section(f"5. {blocks['seams']['question']}"))
    if not seams["candidates"]:
        lines.append(
            "  Литералов, общих для двух языков, нет. Либо репозиторий одноязычный, "
            "либо языки общаются не через строку — и второе стоит проверить руками."
        )
    else:
        lines.append("  Пары языков:")
        lines.extend(
            _rows(
                seams["by_language_pair"][:10],
                [("pair", "пара"), ("count", "литералов")],
                "пар нет",
            )
        )
        lines.append("")
        lines.append("  Кандидаты в швы:")
        for row in seams["candidates"]:
            lines.append(
                f"  {row['literal']}  [{row['kind']}]  языки: {', '.join(row['languages'])}, "
                f"файлов: {row['files']}"
                + ("  (есть объявление с таким именем)" if row["declared_in_code"] else "")
            )
            lines.append(f"      {', '.join(row['examples'])}")

    limits = blocks["limits"]["data"]
    lines.append(_section(f"6. {blocks['limits']['question']}"))
    lines.append(f"  отсеяно файлов: {limits['excluded_files']}")
    lines.extend(
        _rows(
            limits["excluded_by_reason"][:10],
            [("reason", "причина"), ("count", "файлов")],
            "ничего не отсеяно",
        )
    )
    lines.append("")
    lines.append(
        f"  не прочитано по размеру (> {limits['too_big']['limit_bytes'] // 1024} КБ): "
        f"{limits['too_big']['count']}; нечитаемых: {limits['unreadable']['count']}; "
        f"минифицированных: {limits['minified']['count']}"
    )
    lines.append("")
    lines.append("  Что этот отчёт заведомо не видит:")
    for gap in limits["known_gaps"]:
        lines.append(f"    — {gap}")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Разведка репозитория: пять вопросов и честный список того, чего отчёт не видит."
        ),
    )
    parser.add_argument(
        "--root", default=".", help="корень репозитория (по умолчанию текущий каталог)"
    )
    parser.add_argument("--json", dest="json_out", help="куда записать машиночитаемую форму")
    parser.add_argument("--text", dest="text_out", help="куда записать человекочитаемую форму")
    parser.add_argument(
        "--months",
        type=int,
        default=DEFAULT_MONTHS,
        help=(
            "окно археологии в месяцах от даты HEAD; 0 — вся история "
            f"(по умолчанию {DEFAULT_MONTHS})"
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"длина списков в отчёте (по умолчанию {DEFAULT_TOP})",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="дополнительный отсев: сегмент пути, префикс или суффикс (можно повторять)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"нет такого каталога: {args.root}", file=sys.stderr)
        return 2

    report = build_report(root, args.months, args.top, list(args.exclude))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    text = render_text(report)
    if args.text_out:
        Path(args.text_out).write_text(text, encoding="utf-8")
    if not args.text_out:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
