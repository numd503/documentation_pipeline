"""Конфигурация запуска (`docpipe.yaml`).

Отделена от правил классификации (`rules/dotnet.yaml`) намеренно: правила
описывают, *что считать контроллером*, конфигурация — *где искать код и что
из него документировать*. Первое переносится между проектами, второе нет.
"""

import posixpath
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DocLayout = Literal["kind-first", "module-first"]


def _repo_relative(value: str, field: str) -> str:
    """Проверить, что путь репо-относительный и POSIX, и убрать лишние слэши.

    Значения этих полей попадают в `doc_path` каждого узла манифеста, а тот
    обязан быть репо-относительным с POSIX-разделителями даже на Windows.
    Абсолютный путь или `..` дал бы манифест, который невозможно перенести
    между машинами, и обнаружилось бы это не здесь, а на чужом компьютере.
    """
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError(
            f"{field}: путь обязан быть относительным корня репозитория, дано {value!r}"
        )
    if "\\" in value:
        raise ValueError(f"{field}: разделитель — только `/`, даже на Windows; дано {value!r}")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"{field}: выход за корень (`..`) запрещён, дано {value!r}")
    return "/".join(parts)


class UrlRewrite(BaseModel):
    """Что делает с URL прокси модуля по дороге к бэкенду.

    Повторяет `pathRewrite` из `proxy.conf`: у PM `^/pm` → `''`, у админки
    `^/api/` → `/admin/api/`. Пустые поля — законное значение: «проверено,
    преобразования нет». **Отсутствие записи о модуле — другое**: это
    ненастроенный модуль, и отчёт связи обязан назвать его вслух, иначе
    забытая настройка выглядит как исправная связь.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    module: str
    strip_prefix: str = ""
    add_prefix: str = ""


class Discriminator(BaseModel):
    """Где лежит имя списка у обращения к реестру."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    where: Literal["body", "query"] = Field(alias="in")
    name: str


class RegistryCallConfig(BaseModel):
    """Маршрут платформы, у которого смысл вызова определяет не маршрут.

    `api/items/query` с `listInnerName` в теле и `api/items?listInnerName=…`
    в query — один маршрут на много смыслов. Одного правила на оба случая
    не хватает: их два у одного и того же API.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    route: str
    discriminator: Discriminator
    kind: str = ""


class WebConfig(BaseModel):
    """Секция `web` в `docpipe.yaml`: шаг разбора фронтенда.

    Отдельная секция, а не поля верхнего уровня: ключи шага 1 читает и шаг 2,
    и бизнес-слой, и подмешивать к ним настройки одного языка значило бы
    показывать их всем троим.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    roots: list[str] = Field(default_factory=lambda: ["."])
    rules: str = "rules/rules.yaml"
    out: str = "artifacts/doc-tree.web.json"
    link_out: str = "artifacts/web-link.json"

    # Пустой список — законное значение: значит, проверено и ничего
    # не преобразуется. Читается сверху вниз, первое совпадение выигрывает.
    url_rewrite: list[UrlRewrite] = Field(default_factory=list)
    registry_calls: list[RegistryCallConfig] = Field(default_factory=list)

    @field_validator("roots")
    @classmethod
    def _check_roots(cls, value: list[str]) -> list[str]:
        return [_repo_relative(item, "web.roots") for item in value]

    def rewrite_for(self, module: str) -> UrlRewrite | None:
        """Правило модуля. `None` — модуль в таблице не назван вовсе."""
        return next((item for item in self.url_rewrite if item.module == module), None)


class DocpipeConfig(BaseModel):
    """Настройки прогона.

    `enrolled`, `exclude` и scope решают разные задачи, их легко перепутать:
    scope — «что я сейчас перепарсиваю» (влияет на скорость и размер диффа),
    enrolled — «что вообще входит в документацию» (влияет на состав манифеста),
    exclude — «куда не заходить вовсе» (файл не читается и символов не даёт).
    Неenrolled модули всё равно парсятся: их символы нужны для графа наследования,
    а исключённые — нет, поэтому наследование через них рвётся. Это цена за то,
    чтобы не читать чужое дерево: каталог с самим инструментом, вендоренные
    зависимости, выгрузки.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Каталоги, в которых вообще искать исходники. Сужает обход, а не состав
    # документации: что из найденного документировать, решает `enrolled`.
    # Отличается от `--scope` тем, что постоянен и не делает манифест частичным;
    # при обоих заданных сужения складываются.
    roots: list[str] = Field(default_factory=lambda: ["."])
    enrolled: list[str] = Field(default_factory=lambda: ["**"])
    exclude: list[str] = Field(default_factory=list)
    domains: dict[str, str] = Field(default_factory=dict)
    rules: str = "rules/rules.yaml"
    out: str = "artifacts/doc-tree.json"
    cache_dir: str = ".docpipe/cache"

    # Шаг 2. Все со значениями по умолчанию: модель `extra="forbid"`, но
    # существующие конфигурации обязаны продолжать работать.
    templates: str = "templates"
    ownership: str | None = None
    docs_root: str = "docs"

    # Каталог технической документации ВНУТРИ `docs_root`, а не путь целиком.
    # Пара вместо одного значения выбрана ради инварианта «то, что пишет
    # `materialize`, лежит там, где ищет `docs status`»: он держится структурно
    # и нарушить его нельзя. Одним значением (`modules_root: "other/modules"`
    # при `docs_root: "docs"`) прогон писал бы документы туда, где их никто
    # не ищет: каждый навсегда остался бы `missing` и переписывался бы заново
    # на каждом прогоне, молча.
    modules_dir: str = "modules"

    docs_scan_exclude: list[str] = Field(default_factory=list)

    # Раскладка документов. Обе — перестановка одной и той же тройки
    # (модуль, вид, slug), поэтому на **коллизии не влияют вообще**: путь
    # в каждой из них однозначно определяется тройкой, и множество конфликтов
    # у них общее (на ABP — одни и те же 19 путей на 47 узлов). Выбирать
    # приходится не по безопасности, а по тому, как дерево читают:
    #
    #   kind-first    docs/modules/gridservices/Sbt.Cf.Grid.AutoConclusion/x.md
    #   module-first  docs/modules/Sbt.Cf.Grid.AutoConclusion/gridservices/x.md
    #
    # `kind-first` собирает все сущности одного вида в один каталог — это то,
    # ради чего он и выбран по умолчанию: «покажи все grid-сервисы» становится
    # обходом каталога. Плата ровно одна: по префиксу пути больше не выбрать
    # модуль целиком (`docs status МАНИФЕСТ docs/modules/Sample.Common`),
    # потому что его документы разложены по каталогам видов. `module-first`
    # оставлен параметром для тех, кому важнее эта выборка, и как способ
    # не переезжать уже написанным деревом: смена значения меняет `doc_path`
    # у всех узлов сразу.
    doc_layout: DocLayout = "kind-first"

    # Бизнес-слой. `business_root` — параметр, а не константа: на АС CF
    # артефакты инструмента лежат в `docs/ml/docspipe`, и первый каталог
    # бизнес-документов заведут там же. Когда он понадобится другим командам,
    # его вынесут; при параметре это правка одной строки, при константе —
    # правка `doc_path` в каждом документе.
    registries: str | None = None
    business_root: str = "business"

    # Шаг 3. Куда `docpipe worklist` кладёт очередь для внешнего исполнителя.
    # Путь относительно текущего каталога, как `out`, а не относительно `--root`:
    # очередь — артефакт прогона, а не часть документации, и на АС CF она лежит
    # рядом с манифестом, вне дерева документов.
    worklist: str = "artifacts/doc-worklist.json"

    # Шаг `web`. Секция необязательна: репозиторий без фронта её не заводит,
    # и умолчания дают рабочий прогон на репозитории, где фронт один.
    web: WebConfig = Field(default_factory=lambda: WebConfig())

    # `cache_dir` здесь же, хотя в `doc_path` он не попадает: он единственный
    # из «путей прогона», который склеивается с `--root`, а не с текущим
    # каталогом. Без проверки абсолютное значение молча выигрывало бы склейку
    # (`Path(root) / "/tmp/x"` == `/tmp/x`), и кэш уезжал бы за пределы репозитория.
    @field_validator("docs_root", "modules_dir", "business_root", "cache_dir")
    @classmethod
    def _check_repo_relative(cls, value: str, info: Any) -> str:
        return _repo_relative(value, str(info.field_name))

    @field_validator("roots")
    @classmethod
    def _check_roots(cls, value: list[str]) -> list[str]:
        return [_repo_relative(item, "roots") for item in value]

    @property
    def modules_root(self) -> str:
        """Префикс `doc_path` технических документов.

        Собирается здесь, а не в `tree.py`: пара полей и вывод из неё обязаны
        жить в одном месте, иначе шаг 1 и шаг 2 однажды соберут её по-разному.
        """
        return posixpath.join(self.docs_root, self.modules_dir).strip("/")


def candidate_inputs(value: str | Path, config: Path | None) -> list[Path]:
    """Кандидаты `resolve_input` по порядку: текущий каталог, затем каталог `docpipe.yaml`.

    Вынесено отдельной функцией, чтобы сообщение об отказе могло перечислить
    всё, что пробовали: «не найден» с одним путём заставляет гадать, искался ли
    второй, и это ровно тот случай, когда виновата раскладка поставки,
    а не команда.
    """
    here = Path(value)
    if here.is_absolute() or config is None:
        return [here]

    beside = config.parent / here
    return [here] if beside == here else [here, beside]


def resolve_input(value: str | Path, config: Path | None) -> Path:
    """Путь к файлу инструмента, названному ключом `docpipe.yaml`.

    Сначала от текущего каталога, потом от каталога самого `docpipe.yaml`;
    выигрывает первый существующий.

    **Порядок обратным быть не может.** Конфигурации, лежащие в корне
    репозитория, писались с путями от текущего каталога, и там он совпадает
    с каталогом конфигурации. Отдай приоритет второй ступени — на раскладках,
    где каталоги разные, прогон не упал бы, а молча взял другой набор правил
    или шаблонов. Тихо взятый не тот набор дороже отказа.

    Вторая ступень нужна потому, что конфигурация инструмента лежит не в корне
    (на АС CF — `docs/ml/docspipe/…`), а путь внутри неё естественно писать
    относительно неё же: это единственный вид пути, который автор конфигурации
    может проверить глазами, не помня, из какого каталога зовут команду.

    Не найдено нигде — возвращается первый кандидат: сообщение об ошибке
    обязано называть то, что человек написал в конфигурации, а не последнее
    из того, что перебрал инструмент.

    Применяется только к **входам** — файлам, которые инструмент читает
    (`rules`, `web.rules`, `templates`, `registries`, `ownership`). Цели записи
    (`out`, `web.out`, `web.link_out`, `worklist`) второй ступени не получают:
    «первый существующий» для них не значит ничего, а угадывать, куда писать,
    инструмент не должен.
    """
    candidates = candidate_inputs(value, config)
    return next((path for path in candidates if path.exists()), candidates[0])


def load_config(path: Path | None) -> DocpipeConfig:
    """Загрузить конфигурацию; при `None` вернуть значения по умолчанию.

    Пустой YAML-файл равнозначен отсутствию файла.
    """
    if path is None:
        return DocpipeConfig()

    if not path.is_file():
        raise FileNotFoundError(f"Файл конфигурации не найден: {path}")

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return DocpipeConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"Конфигурация должна быть словарём, получено: {type(raw).__name__}")

    # Секция, у которой закомментированы все записи, разбирается YAML как `None`.
    # Без этого фильтра конфигурация падала бы с «Input should be a valid dictionary»:
    # закомментировать записи — самое обычное действие при настройке, и оно
    # не должно выглядеть как поломка.
    return DocpipeConfig.model_validate({k: v for k, v in raw.items() if v is not None})
