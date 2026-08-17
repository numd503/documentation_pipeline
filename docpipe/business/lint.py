"""Линт бизнес-каталога: восемь проверок в фиксированном порядке.

Задача линта — сделать гниение связи видимым **в том PR, который его вызвал**.
Аналитика нет в PR, где переименовали класс; если связь рвётся молча, обнаружат
это через три месяца, и чинить будет некому.

Отсюда деление находок на две группы, и оно важнее самих проверок:

**Дефекты каталога** — то, что автор документа может починить сам: якорь не
разрешается, процесс без точки входа, неоднозначная версия, ссылка на
необъявленную возможность, расхождение команд, битый идентификатор. Они дают
код 1.

**Инвентарные факты** — непокрытые точки входа и записи реестров, не найденные
среди узлов документации. Кода возврата они не меняют, пока их явно не назвали
в `--fail-on`. Требование покрытия 100 % не ставится и не должно ставиться:
у большей части кода бизнес-смысла нет, а линт, красный с первого дня, будет
выключен на второй.
"""

from collections import Counter
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from docpipe.business.catalog import capability_error
from docpipe.business.fingerprint import hashed_anchors
from docpipe.business.model import Catalog
from docpipe.business.resolve import REGISTRY_KIND, ResolveContext, resolve
from docpipe.materialize.ownership import Ownership, owner_of
from docpipe.registry.anchors import ENTRY_KINDS, ResolvedAnchor
from docpipe.route import normalize_route

# Порядок фиксирован и несёт смысл: сначала то, что сломано, потом то, чего
# ещё нет. `--fail-on` принимает только эти имена; значение вне перечня —
# ошибка пользователя, а не пустой фильтр (опечатка в CI иначе дала бы вечно
# зелёную проверку).
CHECKS: Final[tuple[str, ...]] = (
    "unresolved",
    "selector-empty",
    "no-entry",
    "ambiguous-version",
    "unknown-capability",
    "unknown-team",
    "team-mismatch",
    "catalog",
    "registry-unlinked",
    "uncovered",
    "pages-uncovered",
    "features-uncovered",
)

# Проверки, которые сами по себе код возврата не меняют: это состояние
# репозитория, а не дефект каталога.
INFORMATIONAL: Final[frozenset[str]] = frozenset(
    {"registry-unlinked", "uncovered", "pages-uncovered", "features-uncovered"}
)

TOP: Final[int] = 15


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Finding(_Base):
    """Одна находка. `where` — документ или файл реестра, чтобы идти чинить
    было куда; сообщение без адреса чинить не помогает."""

    check: str
    where: str
    message: str


class LintReport(_Base):
    findings: list[Finding] = Field(default_factory=list)
    uncovered_by_team: list[tuple[str, int]] = Field(default_factory=list)
    uncovered_by_kind: list[tuple[str, int]] = Field(default_factory=list)
    entry_points: int = 0
    covered: int = 0

    def failing(self, fail_on: list[str]) -> list[Finding]:
        """Находки, которые обязаны уронить прогон.

        Без `--fail-on` роняют все, кроме инвентарных; с `--fail-on` — ровно
        названные, включая инвентарные.
        """
        if fail_on:
            return [item for item in self.findings if item.check in set(fail_on)]
        return [item for item in self.findings if item.check not in INFORMATIONAL]


def _anchor_team(anchor: ResolvedAnchor, ctx: ResolveContext, ownership: Ownership | None) -> str:
    """Команда записи реестра.

    `@team` объявлен только в `services.config`. Для джобов, workflow
    и обработчиков событий владелец берётся из `ownership.yaml` по реализации —
    и это надо говорить прямо: без правил владения `--team` их не отбирает,
    и пустой результат легко принять за отсутствие.
    """
    if anchor.team:
        return anchor.team
    if ownership is None:
        return "(не задан)"

    for target in anchor.targets:
        node = ctx.nodes_by_id.get(target.node_id or "")
        if node is not None:
            decision = owner_of(node, ownership)
            if decision.team:
                return decision.team
    return "(не задан)"


def _covered_keys(catalog: Catalog) -> set[tuple[str, str, str]]:
    """Ключи записей реестра, на которые ссылается хоть один документ.

    Версия в ключ не входит: документ, описывающий вторую версию workflow,
    покрывает workflow как точку входа. Считать первую версию непокрытой
    значило бы требовать документ на каждую историческую версию.
    """
    keys: set[tuple[str, str, str]] = set()
    for doc in catalog.docs:
        for anchor in doc.anchors:
            registry_kind = REGISTRY_KIND.get(anchor.kind)
            if registry_kind:
                keys.add((registry_kind, anchor.scope or "", anchor.ref))
    return keys


def lint(
    catalog: Catalog,
    anchors: list[ResolvedAnchor],
    ctx: ResolveContext,
    business_root: str,
    ownership: Ownership | None = None,
) -> LintReport:
    """Прогнать все проверки. Порядок находок фиксирован и детерминирован."""
    findings: list[Finding] = []

    # 1. Якорь не разрешается. `upstream` не проверяется никогда: процесс может
    #    начинаться в зоне ответственности другой команды, и требовать
    #    доказательства чужого триггера — значит получить вечно красный линт.
    for doc in catalog.docs:
        for anchor in hashed_anchors(doc):
            if not anchor.verify:
                continue
            resolution = resolve(anchor, ctx)
            if resolution.resolved or resolution.candidates:
                continue
            rungs = ", ".join(resolution.tried) or "нет применимых ступеней"
            findings.append(
                Finding(
                    check="unresolved",
                    where=doc.doc_path,
                    message=(
                        f"точка входа не найдена: {anchor.kind} {anchor.display}"
                        f" (пробовали: {rungs})"
                    ),
                )
            )

    # 1a. Селектор `only` не совпал ни с одной записью якоря. Это НЕ то же
    #     самое, что «точка входа не найдена»: якорь на месте, сузили в пустоту.
    #     Чинится одной строкой, поэтому и находка отдельная — с перечнем того,
    #     кто сейчас на якоре, иначе правку пришлось бы угадывать.
    for doc in catalog.docs:
        for anchor in hashed_anchors(doc):
            if not anchor.verify or anchor.only is None:
                continue
            resolution = resolve(anchor, ctx)
            if not resolution.selector_missed:
                continue
            holders = ", ".join(resolution.candidates) or "никто"
            reason = (
                " Правил владения нет: `only.team` без `ownership.yaml` не сужает ничего."
                if anchor.only.team and ctx.ownership is None
                else ""
            )
            findings.append(
                Finding(
                    check="selector-empty",
                    where=doc.doc_path,
                    message=(
                        f"`only` ({anchor.only.display}) не совпал ни с одной записью"
                        f" якоря {anchor.kind} {anchor.display}."
                        f" Сейчас на якоре: {holders}.{reason}"
                    ),
                )
            )

    # 2. Процесс без точки входа: либо не дописан, либо это не процесс.
    for doc in catalog.docs:
        if doc.kind == "process" and not doc.entry:
            findings.append(
                Finding(
                    check="no-entry",
                    where=doc.doc_path,
                    message="процесс без `entry`: не дописан или это не процесс",
                )
            )

    # 3. Неоднозначная версия. Кандидаты перечисляются, выбор не делается:
    #    состав шагов у версий разный, и молчаливый выбор зафиксировал бы
    #    в хэше произвольную.
    for doc in catalog.docs:
        for anchor in hashed_anchors(doc):
            resolution = resolve(anchor, ctx)
            if resolution.candidates and not resolution.selector_missed:
                found = ", ".join(resolution.candidates)
                findings.append(
                    Finding(
                        check="ambiguous-version",
                        where=doc.doc_path,
                        message=(
                            f"ссылка без `version` при нескольких кандидатах:"
                            f" {anchor.kind} {anchor.ref} — {found}"
                        ),
                    )
                )

    # 4. Ссылка на необъявленную возможность. Сообщение собирается той же
    #    функцией, что и при загрузке каталога: два текста про одно разошлись бы.
    declared = {capability.id for capability in catalog.capabilities}
    capability_lines = {
        capability_error(doc, business_root)
        for doc in catalog.docs
        if doc.capability and doc.capability not in declared
    }
    for line in sorted(capability_lines):
        findings.append(
            Finding(check="unknown-capability", where=line.split(":", 1)[0], message=line)
        )

    # 4a. Селектор ссылается на необъявленную команду. Симметрично тому, как
    #     `load_ownership` отвергает правило с неизвестной командой: там это
    #     ошибка загрузки, а здесь молча ничего не отбиралось бы. Разный
    #     регистр (`ml` против `ML`) — два разных идентификатора, и склеить
    #     их значило бы завести два написания одного имени.
    declared_teams = {team.id for team in ownership.teams} if ownership else set()
    for doc in catalog.docs:
        for anchor in doc.anchors:
            wanted = anchor.only.team if anchor.only else None
            if not wanted or ownership is None or wanted in declared_teams:
                continue
            known = ", ".join(sorted(declared_teams)) or "(список пуст)"
            findings.append(
                Finding(
                    check="unknown-team",
                    where=doc.doc_path,
                    message=(
                        f"якорь {anchor.kind} {anchor.display}: `only.team` называет"
                        f" команду {wanted!r}, которой нет в `ownership.yaml`;"
                        f" объявлены: {known}"
                    ),
                )
            )

    # 5. Расхождение команд: `@team` реестра против `ownership.yaml`
    #    по реализации. Отвечает на вопрос «сервис объявлен нашим, а код чей».
    if ownership is not None:
        for record in anchors:
            if not record.team:
                continue
            for target in record.targets:
                node = ctx.nodes_by_id.get(target.node_id or "")
                if node is None:
                    continue
                decision = owner_of(node, ownership)
                if decision.team and decision.team != record.team:
                    findings.append(
                        Finding(
                            check="team-mismatch",
                            where=record.source_path,
                            message=(
                                f"{record.kind} {record.display}: в реестре команда"
                                f" `{record.team}`, по `ownership.yaml` реализация"
                                f" принадлежит `{decision.team}`"
                            ),
                        )
                    )

    # 6. Дубли и формат идентификаторов — то, что каталог отбраковал при
    #    загрузке. Документы с такими ошибками в `catalog.docs` не попали,
    #    поэтому увидеть их можно только здесь.
    for line in catalog.errors:
        if line not in capability_lines:
            findings.append(Finding(check="catalog", where=line.split(":", 1)[0], message=line))

    # 7. Запись реестра ссылается на тип, не найденный среди узлов документации.
    #    Диагноз «мёртвая запись» здесь поставить нечем: узлами становятся
    #    только enrolled и классифицированные типы, а индекса символов
    #    в манифесте нет.
    for record in anchors:
        for target in record.targets:
            if target.via == "unresolved":
                findings.append(
                    Finding(
                        check="registry-unlinked",
                        where=record.source_path,
                        message=(
                            f"{record.kind} {record.display}: {target.field}"
                            f" = {target.fqn} не найден среди узлов документации"
                        ),
                    )
                )

    # 8. Непокрытые точки входа — главный отчёт линта: он отвечает на вопрос
    #    «сколько ещё писать». Списки сюда не входят никогда: их 289, описывать
    #    их поштучно никто не будет, а красным отчёт был бы всегда.
    covered = _covered_keys(catalog)
    entry_points = [record for record in anchors if record.kind in ENTRY_KINDS]
    uncovered = [
        record
        for record in entry_points
        if (record.kind, record.scope or "", record.ref) not in covered
    ]
    for record in uncovered:
        findings.append(
            Finding(
                check="uncovered",
                where=record.source_path,
                message=f"{record.kind} {record.display}: нет бизнес-документа",
            )
        )

    # 10. Страницы фронта без бизнес-документа. Та же природа, что у `uncovered`:
    #     это состояние работы, а не дефект каталога, и кода возврата оно
    #     не меняет. Считается по манифесту шага `web`; репозиторий без фронта
    #     даёт пустой словарь страниц и ни одной находки.
    anchored = {
        normalize_route(anchor.ref)
        for doc in catalog.docs
        for anchor in doc.anchors
        if anchor.kind == "page"
    }
    for route in sorted(ctx.pages_by_route):
        if route in anchored:
            continue
        findings.append(
            Finding(
                check="pages-uncovered",
                where=f"/{route}" if route else "/",
                message="страница без бизнес-документа",
            )
        )

    # 11. Разделы фронта без бизнес-документа. Та же природа, что у страниц:
    #     состояние работы, а не дефект каталога. Считается по объявленным
    #     разделам — их список человек написал сам, и покрытие меряется по нему.
    anchored_features = {
        anchor.ref.strip()
        for doc in catalog.docs
        for anchor in doc.anchors
        if anchor.kind == "feature"
    }
    for name in sorted(ctx.features_by_name):
        if name in anchored_features:
            continue
        findings.append(
            Finding(
                check="features-uncovered",
                where=name,
                message="раздел без бизнес-документа",
            )
        )

    order = {name: index for index, name in enumerate(CHECKS)}
    return LintReport(
        findings=sorted(findings, key=lambda f: (order[f.check], f.where, f.message)),
        uncovered_by_team=Counter(
            _anchor_team(record, ctx, ownership) for record in uncovered
        ).most_common(),
        uncovered_by_kind=sorted(Counter(record.kind for record in uncovered).items()),
        entry_points=len(entry_points),
        covered=len(entry_points) - len(uncovered),
    )


def format_report(report: LintReport, fail_on: list[str], inventory: bool = True) -> str:
    """Текстовый отчёт. Порядок блоков несёт смысл: сначала сломанное,
    потом то, чего ещё нет.

    `inventory=False` оставляет только находки по документам каталога. Инвентарь
    считается по реестрам и на боевом репозитории занимает сотни строк: пока
    описано пять точек входа из пятисот, единственная нужная строка в нём тонет,
    и отчёт перестают читать целиком — вместе с той частью, которая про дефекты.
    """
    lines: list[str] = []
    failing = {finding.check for finding in report.failing(fail_on)}

    for check in CHECKS:
        if check == "uncovered" or (not inventory and check in INFORMATIONAL):
            continue
        found = [finding for finding in report.findings if finding.check == check]
        if not found:
            continue
        mark = "" if check in failing else "  (не влияет на код возврата)"
        lines += ["", f"{check}: {len(found)}{mark}"]
        lines += [f"  {finding.where}: {finding.message}" for finding in found]

    if not inventory:
        return "\n".join(lines).strip() or "Находок по документам каталога нет."

    lines += [
        "",
        f"Точек входа: {report.entry_points}, описано: {report.covered},"
        f" осталось: {report.entry_points - report.covered}",
    ]
    if report.uncovered_by_kind:
        lines.append("  по видам:")
        lines += [f"    {count:6}  {kind}" for kind, count in report.uncovered_by_kind]
    if report.uncovered_by_team:
        lines.append("  по командам:")
        lines += [f"    {count:6}  {team}" for team, count in report.uncovered_by_team[:TOP]]
        if len(report.uncovered_by_team) > TOP:
            lines.append(f"    {'':6}  и ещё команд: {len(report.uncovered_by_team) - TOP}")

    if not report.findings:
        lines.append("")
        lines.append("Каталог в порядке.")

    return "\n".join(lines).lstrip("\n")
