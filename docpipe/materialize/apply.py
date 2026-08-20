"""Применение плана на диск.

Две фазы строго: план → проверка → запись. При непустых `plan.errors`
не записывается **ничего**: половина обновлённого дерева хуже необновлённого,
потому что о ней никто не узнает.

Частичный сбой прогон не прерывает. Файл только для чтения, переполненный диск,
исчезнувший каталог — ошибка собирается, остальные документы пишутся, в конце
сообщение и код 1. Прерывание на первой ошибке оставляет дерево наполовину
обновлённым.
"""

from dataclasses import dataclass, field
from pathlib import Path

from docpipe.documents import write_atomic
from docpipe.materialize.plan import MaterializePlan, PlannedDoc
from docpipe.materialize.template import DEFAULT_TEMPLATE

BROKEN_SUFFIX = ".md.broken"


@dataclass(frozen=True)
class ApplyResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    relocated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def written(self) -> int:
        return len(self.created) + len(self.updated) + len(self.relocated)


def _relocate(root: Path, doc: PlannedDoc, result: ApplyResult) -> bool:
    """Перенести файл и пересобрать его по новому пути.

    Именно в таком порядке: обратный оставляет окно, в котором падение процесса
    даёт две копии документа или ни одной.
    """
    assert doc.relocate_from is not None
    source = root / doc.relocate_from
    target = root / doc.doc_path
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        source.replace(target)
    except OSError as exc:
        # `Path.replace` через границу файловых систем бросает OSError.
        # В докерных сборках `docs/` бывает томом. Фолбэк на copy+unlink
        # не делается: он возвращает то самое окно, ради закрытия которого
        # выбран rename.
        result.errors.append(
            f"{doc.relocate_from} → {doc.doc_path}: перенос не удался ({exc})."
            " Если каталоги на разных файловых системах, перенесите файл вручную"
        )
        return False

    if doc.content is not None:
        write_atomic(target, doc.content)
    result.relocated.append(f"{doc.relocate_from} → {doc.doc_path}")
    return True


def _force_broken(root: Path, doc: PlannedDoc, result: ApplyResult) -> None:
    """Пересоздать испорченный документ, сохранив исходник рядом.

    Копия не удаляется никогда: в ней единственный экземпляр авторского текста
    и принятого состояния.
    """
    path = root / doc.doc_path
    backup = path.with_suffix(BROKEN_SUFFIX)
    backup.write_bytes(path.read_bytes())
    write_atomic(path, doc.content or "")
    result.backups.append(backup.relative_to(root).as_posix())
    result.updated.append(doc.doc_path)


def apply_plan(
    plan: MaterializePlan, root: Path, dry_run: bool = False, force: bool = False
) -> ApplyResult:
    """Применить план. При непустых `plan.errors` не делается ничего."""
    result = ApplyResult()
    if plan.errors:
        return ApplyResult(errors=list(plan.errors))

    # Переносы идут первыми: остальные документы могут ссылаться на новый путь,
    # и пересобирать их до переезда значило бы записать ссылку в пустоту.
    for doc in plan.documents:
        if doc.file_action != "relocate":
            continue
        if dry_run:
            result.relocated.append(f"{doc.relocate_from} → {doc.doc_path}")
            continue
        _relocate(root, doc, result)

    for doc in plan.documents:
        if doc.file_action == "relocate":
            continue

        if doc.file_action == "unchanged":
            # Файл не открывается на запись вовсе: `mtime` не трогается, иначе
            # прогон даёт тысячи строк в `git status` при включённом фильтре
            # переводов строк.
            result.unchanged.append(doc.doc_path)
            continue

        if doc.file_action == "refuse":
            if not force or doc.content is None:
                result.refused.append(doc.doc_path)
                continue
            if dry_run:
                result.updated.append(doc.doc_path)
                continue
            try:
                _force_broken(root, doc, result)
            except OSError as exc:
                result.errors.append(f"{doc.doc_path}: {exc}")
            continue

        if doc.content is None:
            continue

        # Последний рубеж: `create` означает «файла не было», и если он есть,
        # то план строился по неполной картине дерева (документ не попал
        # в обход). Записать здесь — значит затереть чужой текст без копии,
        # поэтому прогон отказывается независимо от того, кто собрал план.
        # Штатно до этого не доходит: такие пути ловит `shadowed_docs`.
        if doc.file_action == "create" and (root / doc.doc_path).exists():
            result.errors.append(
                f"{doc.doc_path}: файл уже существует, а план считает его новым;"
                " файл не тронут. Причина — документ не попал в обход"
                " (front matter, `docs_scan_exclude`, симлинк, права)"
            )
            continue

        if dry_run:
            (result.created if doc.file_action == "create" else result.updated).append(doc.doc_path)
            continue

        try:
            write_atomic(root / doc.doc_path, doc.content)
        except OSError as exc:
            result.errors.append(f"{doc.doc_path}: {exc}")
            continue
        (result.created if doc.file_action == "create" else result.updated).append(doc.doc_path)

    return result


DIRECTORY_LIMIT = 20


def directory_counts(paths: list[str]) -> list[tuple[str, int]]:
    """Сколько документов приходится на каждый каталог.

    Порядок — по убыванию количества, при равенстве по имени каталога. Явный
    ключ, а не порядок вставки: список идёт в отчёт, а отчёт читают глазами
    и сравнивают между прогонами.
    """
    counts: dict[str, int] = {}
    for path in paths:
        directory = path.rpartition("/")[0] or "."
        counts[directory] = counts.get(directory, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _directory_section(title: str, paths: list[str]) -> list[str]:
    """Раскладка по каталогам, обрезанная сверху.

    Одна цифра «создано: 4820» не отвечает на первый же вопрос настройщика —
    где это окажется. Полный список на боевом репозитории — тысячи строк,
    поэтому показываются самые крупные каталоги, а остаток сворачивается
    в строку: оценку объёма она даёт, а отчёт не топит.
    """
    if not paths:
        return []

    counts = directory_counts(paths)
    width = max(len(directory) for directory, _ in counts[:DIRECTORY_LIMIT])
    lines = ["", title]
    lines += [f"  {directory:<{width}} {count:>5}" for directory, count in counts[:DIRECTORY_LIMIT]]

    hidden = counts[DIRECTORY_LIMIT:]
    if hidden:
        documents = sum(count for _, count in hidden)
        lines.append(f"  и ещё каталогов: {len(hidden)}, документов в них: {documents}")
    return lines


def format_result(plan: MaterializePlan, result: ApplyResult, dry_run: bool = False) -> str:
    """Отчёт о прогоне."""
    if plan.errors:
        return "\n".join(
            ["Прогон отменён, не записано ничего:", *(f"  {error}" for error in plan.errors)]
        )

    head = "Что было бы сделано:" if dry_run else "Сделано:"
    lines = [
        head,
        f"  создано:      {len(result.created)}",
        f"  обновлено:    {len(result.updated)}",
        f"  перенесено:   {len(result.relocated)}",
        f"  без изменений:{len(result.unchanged):>3}",
    ]
    if result.refused:
        lines.append(f"  отказано:     {len(result.refused)}")

    lines += _directory_section(
        "Где было бы создано:" if dry_run else "Где создано:", result.created
    )
    # Обновлённые той же раскладкой, а не списком: «обновлено: 4820» не отвечает
    # на первый вопрос — что именно перепишут, — а полный список на боевом
    # объёме топит отчёт. Поимённо один документ показывает `docs explain`.
    lines += _directory_section(
        "Где было бы обновлено:" if dry_run else "Где обновлено:", result.updated
    )

    if plan.substituted:
        # Не «замечание», а отдельный раздел с числами. Подстановка — решение
        # по умолчанию, а не покрытие: невидимая, она превращает опечатку
        # в `template` из отказа прогона в молча документированный вид.
        width = max(len(name) for name in plan.substituted)
        lines += ["", f"Своего скелета нет, применён `{DEFAULT_TEMPLATE}`:"]
        lines += [f"  {name:<{width}} {count:>5}" for name, count in plan.substituted.items()]

    counts = plan.counts()
    if counts:
        lines += ["", "Состояние документов:"]
        lines += [f"  {status:<12} {count:>5}" for status, count in counts.items()]

    for title, rows in (
        ("Перенесено:", result.relocated),
        ("Отказ, файл не тронут:", result.refused),
        ("Копии испорченных документов:", result.backups),
        ("Замечания по переносам:", plan.notes),
        ("Ошибки записи:", result.errors),
    ):
        if rows:
            lines += ["", title] + [f"  {row}" for row in rows]

    if plan.manifest_partial:
        lines += [
            "",
            "Внимание: манифест частичный (--scope). Статусы вне скоупа недостоверны.",
        ]

    return "\n".join(lines)
