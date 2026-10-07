"""Вход шага 2 библиотекой: манифест, шаблоны, владение, бизнес-ссылки, план.

Команды шага 2 (`materialize`, `docs status/explain/accept/adopt`, `worklist`)
читают одно и то же, и раньше эта сборка жила в `cli.py` под именем `_prepare`.
Пока у неё был один потребитель — командная строка, — этого хватало. Сервер
настройки (S27) и сводка состояния (S24) зовут тот же вход без CLI, и копия
в сервере разошлась бы с оригиналом на первом же ключе: ровно так однажды
разошлись три копии `_prepare` — ключ, добавленный в одну, до остальных
не доезжал, а писателем документов была та, что отстала бы незаметнее всех.

Поэтому здесь нет ни печати, ни `typer.Exit`: ошибка входа — `Step2Error`
с кодом возврата, предупреждения — список в `Step2Inputs.warnings`. Как их
показать, решает вызывающий: CLI печатает в stderr и выходит с кодом, сервер
отдаёт ответом.

Модуль верхнего уровня, а не `docpipe/materialize/`, и это не вкусовщина:
бизнес-ссылки импортируют `docpipe.business`, а пакету шага 2 это запрещено
(`test_materialize_does_not_import_business`) — шаг 2 обязан работать без
бизнес-слоя, и обратный индекс приходит в него данными.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from docpipe.business import build_context as build_resolve_context
from docpipe.business import load_catalog
from docpipe.business.build import backlinks
from docpipe.config import DocpipeConfig, resolve_input
from docpipe.materialize.build import BuildContext, build_context
from docpipe.materialize.ownership import Ownership, load_ownership
from docpipe.materialize.plan import (
    ExistingDoc,
    MaterializePlan,
    PlanOptions,
    build_plan,
    check_links,
    scan_docs,
    shadowed_docs,
    with_links,
)
from docpipe.materialize.template import load_templates
from docpipe.model import Manifest
from docpipe.registry.anchors import read_anchors

# Префикс предупреждений о бизнес-ссылках. Один на все строки: по нему
# предупреждение отличают от остального stderr, а S24 — от дефектов шага 2.
BUSINESS_LINKS_PREFIX = "бизнес-ссылки: "


class Step2Error(Exception):
    """Вход шага 2 не собрался. `code` — код возврата команды.

    2 — ошибка пользователя: манифест, шаблоны или владение не читаются,
    неизвестная команда в `--team`. Отдельный класс, а не `ValueError`:
    вызывающему надо отличить «вход не собрался» от сбоя внутри плана,
    который под вывеской ошибки входа потерял бы трассировку.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Step2Inputs:
    """Всё, что команды шага 2 читают из конфигурации и с диска.

    Возвращается целиком, а не тройкой-четвёркой: раньше `materialize`,
    `docs status` и `_prepare` собирали одно и то же тремя копиями одного
    блока, и ключ конфигурации, добавленный в одну, до остальных не доезжал.

    `warnings` — то, что прогон не роняет, но о чём молчать нельзя: ошибки
    реестров и бизнес-каталога (строки с префиксом «бизнес-ссылки:»).
    """

    settings: DocpipeConfig
    manifest: Manifest
    ownership: Ownership | None
    existing: list[ExistingDoc]
    plan: MaterializePlan
    warnings: list[str] = field(default_factory=list)


def load_manifest(path: Path) -> Manifest:
    """Прочитать манифест шага 1. Не читается — `Step2Error` с кодом 2."""
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Step2Error(2, str(exc)) from exc


def check_teams(teams: Sequence[str], ownership: Ownership | None) -> None:
    """Отвергнуть неизвестные команды.

    Без проверки опечатка в `--team` сужает выборку до пустой, и команда
    рапортует «документов нет» — неотличимо от честного «у этой команды
    документов нет». Раньше это ловил только `materialize`; `docs status`
    и `worklist` с тем же флагом молчали.
    """
    if not teams:
        return
    known = {item.id for item in ownership.teams} if ownership else set()
    unknown = sorted(set(teams) - known)
    if unknown:
        listing = ", ".join(sorted(known)) or "(правила владения не заданы)"
        raise Step2Error(2, f"Неизвестные команды: {', '.join(unknown)}; известны: {listing}")


def _business_links(
    context: BuildContext,
    manifest: Manifest,
    root: Path,
    settings: DocpipeConfig,
    config: Path | None,
    ownership: Ownership | None,
) -> tuple[BuildContext, list[str]]:
    """Досыпать в контекст шага 2 обратный индекс бизнес-каталога.

    Индекс строится здесь, а не внутри `materialize`: пакет шага 2 не импортирует
    бизнес-слой и получает готовые данные. Без заданных `registries` шаг 2
    работает ровно как прежде — раздела «Бизнес-контекст» не появляется вовсе.

    Владение приходит из `prepare` — тот же объект, по которому строится план.
    Своё чтение здесь брало только ключ `ownership`, мимо `--ownership`, и глотало
    ошибку разбора: селектор `only.team` молча не сужал ничего, а план при этом
    раскладывал документы по командам из другого файла.

    Неготовность бизнес-слоя прогон шага 2 не роняет: реестры могут быть
    описаны раньше, чем появится первый бизнес-документ, и отказ материализовать
    техническую документацию из-за этого был бы наказанием не за то. Но и не
    молчит: ошибки реестров и каталога — предупреждения. Выброшенная ошибка
    реестра выглядела бы как «у этого класса нет бизнес-контекста».
    """
    if not settings.registries:
        return context, []

    try:
        anchors, registry_errors = read_anchors(
            manifest, resolve_input(settings.registries, config), root
        )
        catalog = load_catalog(root, settings.business_root)
        links = backlinks(
            catalog, build_resolve_context(anchors, manifest, root=root, ownership=ownership)
        )
    except (OSError, ValueError) as exc:
        return context, [f"{BUSINESS_LINKS_PREFIX}каталог не прочитан, раздел не собран: {exc}"]

    # Порядок — как у `business build`: сначала каталог, затем реестры.
    lines = sorted(catalog.errors) + sorted(registry_errors)
    warnings = [f"{BUSINESS_LINKS_PREFIX}{line}" for line in lines]
    return replace(context, business_root=settings.business_root, business_links=links), warnings


def prepare(
    manifest: Manifest,
    root: Path,
    settings: DocpipeConfig,
    config: Path | None,
    *,
    templates_dir: Path | None = None,
    ownership_file: Path | None = None,
    teams: Sequence[str] = (),
    accept: Sequence[str] = (),
    force: bool = False,
    links: bool = False,
) -> Step2Inputs:
    """Собрать вход шага 2 и план по уже прочитанным манифесту и конфигурации.

    `config` — путь к `docpipe.yaml`, от каталога которого ищутся входы
    (`resolve_input`); `None` — конфигурации нет. Флаги важнее ключей:
    `templates_dir` и `ownership_file` замещают `templates` и `ownership`.

    `links=True` приписывает плану битые ссылки авторских секций — так их
    видят `docs status`, `docs explain` и `worklist`. `materialize` и приёмка
    их не спрашивают: ссылки не меняют ни записи, ни статуса.
    """
    templates_path = templates_dir or resolve_input(settings.templates, config)
    ownership_path = ownership_file or (
        resolve_input(settings.ownership, config) if settings.ownership else None
    )

    try:
        templates = load_templates(templates_path)
        ownership = load_ownership(ownership_path) if ownership_path else None
    except (OSError, ValueError) as exc:
        raise Step2Error(2, str(exc)) from exc

    check_teams(teams, ownership)

    examples = frozenset(path.stem for path in (templates_path / "examples").glob("*.md"))
    context, warnings = _business_links(
        build_context(manifest, templates, examples, templates_path.as_posix()),
        manifest,
        root,
        settings,
        config,
        ownership,
    )
    existing = scan_docs(root, settings.docs_root, settings.docs_scan_exclude)
    plan = build_plan(
        manifest,
        existing,
        templates,
        context,
        ownership,
        PlanOptions(
            docs_root=settings.docs_root,
            modules_root=settings.modules_root,
            web_modules_root=settings.web_modules_root,
            doc_layout=settings.doc_layout,
            teams=tuple(teams),
            accept=tuple(accept),
            force=force,
            docs_scan_exclude=tuple(settings.docs_scan_exclude),
            # Проверка по диску, а не по обходу: узел без файла и узел, чей файл
            # обход не увидел, — разные состояния, и различить их можно только так.
            shadowed=tuple(shadowed_docs(root, manifest, existing)),
        ),
    )
    if links:
        plan = with_links(plan, check_links(existing, root))
    return Step2Inputs(
        settings=settings,
        manifest=manifest,
        ownership=ownership,
        existing=existing,
        plan=plan,
        warnings=warnings,
    )


__all__ = [
    "BUSINESS_LINKS_PREFIX",
    "Step2Error",
    "Step2Inputs",
    "check_teams",
    "load_manifest",
    "prepare",
]
