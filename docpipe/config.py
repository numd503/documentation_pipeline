"""Конфигурация запуска (`docpipe.yaml`).

Отделена от правил классификации (`rules/dotnet.yaml`) намеренно: правила
описывают, *что считать контроллером*, конфигурация — *где искать код и что
из него документировать*. Первое переносится между проектами, второе нет.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class DocpipeConfig(BaseModel):
    """Настройки прогона.

    `enrolled` и scope решают разные задачи, их легко перепутать:
    scope — «что я сейчас перепарсиваю» (влияет на скорость и размер диффа),
    enrolled — «что вообще входит в документацию» (влияет на состав манифеста).
    Неenrolled модули всё равно парсятся: их символы нужны для графа наследования.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    roots: list[str] = Field(default_factory=lambda: ["."])
    enrolled: list[str] = Field(default_factory=lambda: ["**"])
    domains: dict[str, str] = Field(default_factory=dict)
    rules: str = "rules/dotnet.yaml"
    out: str = "artifacts/doc-tree.json"
    cache_dir: str = ".docpipe/cache"


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

    return DocpipeConfig.model_validate(raw)
