"""Контекст команд `setup`: настройка и прогоны, собранные в одном месте.

Команды `setup` (кандидаты, `explain`, дальше `link`, `status`, `review`
и сервер настройки) читают одни и те же прогоны: шаг 1, шаг `web`, план
шага 2, владение. Собирай их каждая команда сама — повторилась бы история
трёх копий `_prepare`: ключ, учтённый в одной, не доезжает до остальных,
и `setup status` говорит о другом прогоне, чем `setup explain`. Поэтому
прогоны собирает только `SetupContext`.

Прогоны ленивые и запоминаются: команде, которой нужен шаг 1, шаг `web`
не нужен вовсе (и его отказ из-за `pages.yaml` её не касается), а второе
обращение к прогону его не повторяет.

**Только память, никогда артефакты на диске.** Агент правит настройку и сразу
зовёт команду (Р-1 плана); манифест на диске собран прошлой настройкой,
и отчёт по нему показал бы, что правка ничего не изменила.
"""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml
from pydantic import ValidationError

from docpipe.classify import Ruleset, load_ruleset
from docpipe.config import DocpipeConfig, ScopeConflict, load_config, resolve_input
from docpipe.emit import ScanResult
from docpipe.emit import run as run_scan
from docpipe.materialize.ownership import Ownership, load_ownership
from docpipe.step2 import Step2Error, Step2Inputs, prepare
from docpipe.web.calls import WrapperConflict
from docpipe.web.overrides import Overrides, configured_pages, load_page_overrides
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web_scan


class InputError(Exception):
    """Вход негоден: настройка, файл правил, `pages.yaml`, аргумент команды.

    Отдельный класс, а не `ValueError`: CLI отвечает на него кодом 2
    с сообщением, а любое другое исключение — это сбой, и прятать его
    под «ошибкой конфигурации» значит потерять трассировку.

    Шаг 2 сюда не входит: план, который не собрался (нет скелетов, битый
    `ownership.yaml`), — `Step2Error`, и команды `setup` показывают его
    в отчёте, а не падают: остальные факты агенту нужны и тогда.
    """


@dataclass(frozen=True)
class SetupContext:
    """Корень, настройка и откуда она прочитана — и прогоны по ним.

    `config` — путь к `docpipe.yaml`: от его каталога разрешаются входы
    (`resolve_input`), и без него сервер настройки нашёл бы другой набор
    правил, чем CLI, позванный из того же каталога. `use_cache` — кэш
    разбора под `--root`, как у `scan`; тесты его выключают, чтобы кэш
    не ложился в фикстуру.
    """

    root: Path
    settings: DocpipeConfig
    config: Path | None = None
    use_cache: bool = True

    @classmethod
    def build(
        cls, root: Path, config_path: Path | None, *, use_cache: bool = True
    ) -> "SetupContext":
        """Прочитать настройку. Не читается — `InputError` с причиной."""
        try:
            settings = load_config(config_path)
        except (OSError, ValueError) as exc:
            raise InputError(str(exc)) from exc
        return cls(root, settings, config_path, use_cache)

    @property
    def cache_dir(self) -> Path | None:
        return self.root / self.settings.cache_dir if self.use_cache else None

    @property
    def config_label(self) -> str:
        """Как назвать файл настройки в ответе: путь, по которому его прочитали."""
        return self.config.as_posix() if self.config is not None else "docpipe.yaml"

    # ----------------------------------------------------------------------------------
    # Шаг 1
    # ----------------------------------------------------------------------------------

    @cached_property
    def rules_file(self) -> Path:
        return resolve_input(self.settings.rules, self.config)

    @cached_property
    def ruleset(self) -> Ruleset:
        try:
            return load_ruleset(self.rules_file, "dotnet")
        except (OSError, ValueError) as exc:
            raise InputError(f"набор правил не читается: {exc}") from exc

    @cached_property
    def scan(self) -> ScanResult:
        """Прогон шага 1 тем же путём, что у `scan`: те же правила, тот же кэш."""
        try:
            return run_scan(self.root, self.settings, self.ruleset, self.cache_dir)
        except ScopeConflict as exc:
            # Противоречие `enrolled`/`not_enrolled` — ошибка настройки, как и у `scan`.
            raise InputError(str(exc)) from exc

    # ----------------------------------------------------------------------------------
    # Шаг `web`
    # ----------------------------------------------------------------------------------

    @cached_property
    def web_rules_file(self) -> Path:
        return resolve_input(self.settings.web.rules, self.config)

    @cached_property
    def web_ruleset(self) -> Ruleset:
        try:
            return load_ruleset(self.web_rules_file, "web")
        except (OSError, ValueError) as exc:
            raise InputError(f"набор правил фронта не читается: {exc}") from exc

    @cached_property
    def pages_file(self) -> Path | None:
        """`web.pages`, разрешённый так же, как у `web scan`. Ключ пуст — `None`."""
        try:
            return configured_pages(self.settings, self.config)
        except OSError as exc:
            raise InputError(str(exc)) from exc

    @cached_property
    def overrides(self) -> Overrides:
        """Ручной состав страниц — тем же `load_page_overrides`, что у `web scan`.

        Путь отдаёт `pages_file`, а не второе разрешение ключа: файл, который
        команда назовёт в ответе, и файл, правила которого легли в прогон,
        обязаны совпадать. Ключ пуст — пустые правила, а не отказ.
        """
        try:
            return load_page_overrides(self.pages_file, self.settings, self.config)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise InputError(f"ручной состав страниц не читается: {exc}") from exc

    @cached_property
    def web(self) -> WebScanResult:
        """Прогон шага `web` с ручным составом страниц — как у `web scan`.

        Один на всех потребителей фронта: `setup explain`, кандидаты
        `features` (им нужен и `overrides` — отметка «уже объявлен»)
        и виды вызовов: `registry-calls`, `http-wrappers`, `url-builders`.
        Набор правил читается раньше `pages.yaml`, поэтому и отказы
        приходят в том же порядке, что у `web scan`.
        """
        ruleset, overrides = self.web_ruleset, self.overrides
        try:
            return run_web_scan(self.root, self.settings, ruleset, self.cache_dir, overrides)
        except ValidationError:
            # Модель, не собравшаяся внутри прогона, — сбой, а не вход: под
            # «ошибкой конфигурации» он потерял бы трассировку. `ValidationError`
            # — подкласс `ValueError`, и без этой ветки его поймала бы следующая.
            raise
        except WrapperConflict as exc:
            # Вызов совпал с двумя записями `web.http_wrappers` — ошибка
            # настройки, а не ручного состава страниц.
            raise InputError(str(exc)) from exc
        except ValueError as exc:
            # Неоднозначное правило снятия — тот же отказ, что у `web scan`:
            # выбор наугад значил бы, что инструмент сам решает, какую страницу убрать.
            raise InputError(f"ошибка в ручном составе страниц: {exc}") from exc

    # Здесь S21 добавит `link`: сведение `scan` и `web` с `web.url_rewrite`
    # и секцией `link` (`web.link.build_report`), — тем же путём, что `web link`.

    # ----------------------------------------------------------------------------------
    # Шаг 2 и владение
    # ----------------------------------------------------------------------------------

    @cached_property
    def ownership_file(self) -> Path | None:
        if not self.settings.ownership:
            return None
        return resolve_input(self.settings.ownership, self.config)

    @cached_property
    def ownership(self) -> Ownership | None:
        """Правила владения; ключ не задан — `None`. Не читаются — `Step2Error`."""
        if self.ownership_file is None:
            return None
        try:
            return load_ownership(self.ownership_file)
        except (OSError, ValueError) as exc:
            raise Step2Error(2, str(exc)) from exc

    @cached_property
    def plan(self) -> Step2Inputs:
        """План шага 2 по манифесту шага 1 в памяти. Не собрался — `Step2Error`."""
        return prepare(self.scan.manifest, self.root, self.settings, self.config)

    @cached_property
    def web_plan(self) -> Step2Inputs:
        """План шага 2 по манифесту фронта в памяти — как `docs status` по `web.out`."""
        return prepare(self.web.manifest, self.root, self.settings, self.config)


__all__ = ["InputError", "SetupContext"]
