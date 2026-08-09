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
    rules: str = "rules/dotnet.yaml"
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
