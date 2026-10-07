"""Кандидаты в ключи настройки: из чего строится вопрос человеку.

Ключи вроде `di_methods` нельзя заполнить умолчанием: имя, придуманное по
одному репозиторию, на другом совпадёт не с тем вызовом. Но и оставить их
пустыми нельзя молча — без `di_methods` на squidex видно 67 регистраций
из 421, и ноль незаметен. Поэтому инструмент не угадывает значение, а
считает факты, по которым его предлагает агент, и человек решает.

Каждый вид кандидатов — функция над результатом прогона, возвращающая
модель отчёта, и форматтер текста. `candidates()` — единая точка входа
для CLI и сервера настройки (S27): вид → нужный прогон → отчёт.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig, resolve_input
from docpipe.dotnet.di import is_standard_method
from docpipe.emit import ScanResult
from docpipe.emit import run as run_scan
from docpipe.hashing import stable_json_dumps
from docpipe.model import Member, RegistrationCall, Symbol

# Длина страницы по умолчанию. Агент контура обрезает вывод инструмента,
# и «таких нет» без `total` неотличимо от «не показали» (правило 5 плана).
DEFAULT_LIMIT: Final = 20

# Метод, названный с типом хотя бы дважды. Один вызов — не обёртка, которую
# стоит заводить в настройку, а лямбда-форма без типа регистрацией не станет
# и с ключом (`dotnet/di.py`, `_types`).
MIN_CALLS_WITH_TYPES: Final = 2

_TOP_RECEIVERS: Final = 3
_EXAMPLES: Final = 3


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputError(Exception):
    """Вход прогона негоден: неизвестный вид, отрицательные `limit`/`offset`, файл правил.

    Отдельный класс, а не `ValueError`: CLI отвечает на него кодом 2
    с сообщением, а любое другое исключение — это сбой, и прятать его
    под «ошибкой конфигурации» значит потерять трассировку.
    """


# --------------------------------------------------------------------------------------
# Кандидаты в `di_methods`
# --------------------------------------------------------------------------------------


class DiMethodCandidate(_Base):
    """Метод `Add*`, похожий на самодельную обёртку регистрации.

    `receiver_overlap` — доля вызовов, у которых получатель тот же, на котором
    в этом репозитории зовут стандартные `Add*`. Признак из данных
    репозитория, а не список имён: `AddSingletonAs` на squidex зовут
    на `services` в 271 случае из 275, `AddField` — на `schema`, `AddDays` —
    на дате.

    `declared_in` — необязательная подсказка: главную обёртку по объявлениям
    не найти, на squidex `AddSingletonAs` объявлен в пакете, а 41 объявленный
    `AddSquidex*(this IServiceCollection …)` — агрегаторы, а не обёртки.
    """

    method: str
    calls: int
    calls_with_types: int
    files: int
    receivers: list[tuple[str, int]]
    receiver_overlap: float
    declared_in: str | None = None
    configured: bool
    examples: list[str]


class DiMethodCandidates(_Base):
    """Отчёт `setup candidates di-methods`.

    `standard_calls` и `standard_receivers` — база, от которой считается
    `receiver_overlap`. Без неё ноль пересечения у всех кандидатов
    неотличим от репозитория, где стандартных регистраций нет вовсе: тогда
    признак молчит, и порядок решает только число вызовов с типом.
    """

    schema_version: Literal["1.0"] = "1.0"
    standard_calls: int
    standard_receivers: list[tuple[str, int]]
    total: int
    offset: int
    items: list[DiMethodCandidate]


def _top(counter: Counter[str], size: int) -> list[tuple[str, int]]:
    """Самые частые значения: по убыванию счёта, при равенстве — по имени."""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:size]


def _member_file(symbol: Symbol, member: Member) -> str | None:
    """Файл, в котором объявлен член.

    У члена своего пути нет: при слиянии `partial` он теряется
    (`dotnet/resolve.py`). Ищется по строке среди частей типа; строки двух
    частей из разных файлов могут перекрыться, и тогда берётся меньший путь —
    подсказке хватает детерминизма, а точность здесь не обещана.
    """
    spans = [span for span in symbol.sources if span.start <= member.line <= span.end]
    if spans:
        return min(span.path for span in spans)
    return min((span.path for span in symbol.sources), default=None)


def _extension_declarations(index: dict[str, Symbol]) -> dict[str, str]:
    """Имя метода-расширения → файл объявления (меньший путь, если их несколько).

    Признак расширения — `this ` в `Member.signature` при модификаторе
    `static`. `Graph.identity.parameter_types` для этого не годится: он
    выбрасывает `this` (`graph/identity.py`), и расширение там неотличимо
    от обычного статического метода.
    """
    found: dict[str, str] = {}
    for symbol in index.values():
        for member in symbol.members:
            if member.kind != "method" or "static" not in member.modifiers:
                continue
            if "this " not in member.signature:
                continue
            path = _member_file(symbol, member)
            if path is None:
                continue
            known = found.get(member.name)
            if known is None or path < known:
                found[member.name] = path
    return found


def _typed(call: RegistrationCall) -> bool:
    return call.type_args > 0 or call.typeof_args > 0


def _page[T](items: list[T], limit: int, offset: int) -> list[T]:
    """Страница списка. `limit = 0` — до конца, как у `symbols --limit 0`."""
    return items[offset:] if limit == 0 else items[offset : offset + limit]


def di_method_candidates(
    scan: ScanResult,
    settings: DocpipeConfig,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> DiMethodCandidates:
    """Кандидаты в `di_methods` по вызовам `Add*` этого прогона.

    Стандартные имена (`AddScoped`, `TryAddSingleton`…) в кандидаты не идут —
    они известны без настройки, — но по ним считается база получателей.
    Порядок — `(-round(receiver_overlap, 2), -calls_with_types, method)`:
    пересечение отделяет обёртку от `AddField`, число вызовов с типом —
    частую обёртку от редкой.
    """
    standard: Counter[str] = Counter()
    standard_calls = 0
    by_method: defaultdict[str, list[tuple[str, RegistrationCall]]] = defaultdict(list)
    for path, call in scan.registration_calls:
        if is_standard_method(call.method):
            standard_calls += 1
            # Неизвестный получатель базой не считается: пустая строка
            # совпала бы с любым другим неизвестным и дала бы пересечение
            # там, где о получателе не известно ничего.
            if call.receiver:
                standard[call.receiver] += 1
        else:
            by_method[call.method].append((path, call))

    configured = frozenset(settings.di_methods)
    declarations = _extension_declarations(scan.index)

    found: list[DiMethodCandidate] = []
    for method, calls in by_method.items():
        with_types = sum(1 for _, call in calls if _typed(call))
        if with_types < MIN_CALLS_WITH_TYPES:
            continue
        overlap = sum(1 for _, call in calls if call.receiver in standard) / len(calls)
        # Примеры — сначала вызовы с типом: кандидат стоит в списке из-за них,
        # и три лямбды подряд показали бы человеку не ту форму.
        shown = sorted(calls, key=lambda item: (not _typed(item[1]), item[0], item[1].line))
        found.append(
            DiMethodCandidate(
                method=method,
                calls=len(calls),
                calls_with_types=with_types,
                files=len({path for path, _ in calls}),
                receivers=_top(Counter(call.receiver for _, call in calls), _TOP_RECEIVERS),
                receiver_overlap=round(overlap, 4),
                declared_in=declarations.get(method),
                configured=method in configured,
                examples=[f"{path}:{call.line}" for path, call in shown[:_EXAMPLES]],
            )
        )

    found.sort(key=lambda c: (-round(c.receiver_overlap, 2), -c.calls_with_types, c.method))
    return DiMethodCandidates(
        standard_calls=standard_calls,
        standard_receivers=_top(standard, _TOP_RECEIVERS),
        total=len(found),
        offset=offset,
        items=_page(found, limit, offset),
    )


def _receivers_text(receivers: list[tuple[str, int]]) -> str:
    return ", ".join(f"{name or '—'} {count}" for name, count in receivers)


def format_di_methods(report: DiMethodCandidates) -> str:
    lines = [
        f"Кандидаты в di_methods: {report.total} "
        f"(нестандартные Add*, названные с типом не меньше {MIN_CALLS_WITH_TYPES} раз)."
    ]
    if report.standard_calls:
        lines.append(
            f"Стандартные регистрации: {report.standard_calls}; "
            f"получатели: {_receivers_text(report.standard_receivers) or '—'}."
        )
    else:
        lines.append(
            "Стандартных регистраций нет: пересечение получателей у всех кандидатов "
            "нулевое и ничего не говорит, порядок задаёт только число вызовов с типом."
        )

    for item in report.items:
        mark = "  [уже в di_methods]" if item.configured else ""
        lines += [
            "",
            f"{item.method}{mark}",
            f"  вызовов {item.calls}, с типом {item.calls_with_types}, файлов {item.files}; "
            f"пересечение получателей {item.receiver_overlap:.2f}",
            f"  получатели: {_receivers_text(item.receivers)}",
            f"  объявлен: {item.declared_in or 'не в репозитории'}",
            f"  примеры: {', '.join(item.examples)}",
        ]

    lines.append("")
    lines.append(_page_line(report.total, report.offset, len(report.items)))
    return "\n".join(lines) + "\n"


def _page_line(total: int, offset: int, shown: int) -> str:
    """Строка продолжения: без неё обрезанный список читается как полный."""
    if total == 0:
        return "Кандидатов нет."
    if shown == 0:
        return f"Показано 0 из {total}: --offset {offset} за концом списка."
    line = f"Показаны {offset + 1}–{offset + shown} из {total}."
    if offset + shown < total:
        line += f" Дальше: --offset {offset + shown}."
    return line


# --------------------------------------------------------------------------------------
# Вид кандидатов → прогон → отчёт
# --------------------------------------------------------------------------------------

# Объединение отчётов всех видов: следующие задачи (S12–S14) дописывают сюда
# свои модели, а в `_KINDS` — свои функции.
type CandidateReport = DiMethodCandidates


@dataclass(frozen=True)
class CandidateInputs:
    """Что нужно любому виду: корень, настройка и откуда она прочитана.

    `config` — путь к `docpipe.yaml`: от его каталога разрешаются входы
    (`resolve_input`), и без него сервер настройки нашёл бы другой набор
    правил, чем CLI, позванный из того же каталога.
    """

    root: Path
    settings: DocpipeConfig
    config: Path | None = None
    use_cache: bool = True


def _scan(inputs: CandidateInputs) -> ScanResult:
    """Прогон шага 1 тем же путём, что у `scan`: те же правила, тот же кэш."""
    try:
        ruleset = load_ruleset(resolve_input(inputs.settings.rules, inputs.config), "dotnet")
    except (OSError, ValueError) as exc:
        raise InputError(f"набор правил не читается: {exc}") from exc
    cache_dir = inputs.root / inputs.settings.cache_dir if inputs.use_cache else None
    return run_scan(inputs.root, inputs.settings, ruleset, cache_dir)


def _di_methods(inputs: CandidateInputs, limit: int, offset: int) -> CandidateReport:
    return di_method_candidates(_scan(inputs), inputs.settings, limit=limit, offset=offset)


_KINDS: Final[dict[str, Callable[[CandidateInputs, int, int], CandidateReport]]] = {
    "di-methods": _di_methods,
}

KINDS: Final[tuple[str, ...]] = tuple(sorted(_KINDS))


def candidates(
    kind: str, inputs: CandidateInputs, *, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> CandidateReport:
    """Кандидаты одного вида. Неизвестный вид и отрицательная страница — `InputError`."""
    build = _KINDS.get(kind)
    if build is None:
        raise InputError(f"неизвестный вид кандидатов {kind!r}; известны: {', '.join(KINDS)}")
    if limit < 0 or offset < 0:
        raise InputError("limit и offset не бывают отрицательными")
    return build(inputs, limit, offset)


def format_candidates(report: CandidateReport) -> str:
    """Текст для человека. Вид отчёта определяется моделью, а не флагом."""
    return format_di_methods(report)


def candidates_json(report: CandidateReport) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))
