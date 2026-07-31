"""Конфигурация запуска (`docpipe.yaml`).

Отделена от правил классификации (`rules/dotnet.yaml`) намеренно: правила
описывают, *что считать контроллером*, конфигурация — *где искать код и что
из него документировать*. Первое переносится между проектами, второе нет.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DocLayout = Literal["kind-first", "module-first"]


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
