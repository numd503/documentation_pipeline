# Фаза 10: .NET

## Цель

У каждого типа .NET в области — решение: вид документа или «не
документируем» с причиной человека. Самодельные обёртки регистрации DI
и интерфейсы диспетчеризации записаны: без них связывание пусто молча.

## Инструменты

- `setup_candidates` (`docpipe setup candidates`), виды `di-methods`
  и `dispatch-interfaces`;
- `setup_status` — `dotnet.undecided` (кластеры `module` и `last_word`),
  `parse.errors` (кластер `directory`), охват решений (`coverage`);
- `setup_stats` (`docpipe scan --stats`), `lang: cs` — срезы, «последнее слово»;
- `setup_symbols` (`docpipe symbols`), `lang: cs`, `state: undecided` —
  базы, атрибуты, члены и путь каждого символа группы;
- `setup_explain` (`docpipe setup explain`) — какое правило победило.

## Шаги

1. **Обёртки DI** — `setup_candidates`, `kind: di-methods`. Вверху ответа
   база: `standard_calls` и `standard_receivers`. Кандидат с высоким
   `receiver_overlap` и многими `calls_with_types` — почти наверняка
   обёртка: открой `examples` и `declared_in`. Видишь регистрацию в
   контейнере (`AddSingletonAs<X>().As<I>()`) — пиши `di_methods` сам,
   `reason` — адрес доказательства. Нулевое пересечение — метод другого
   объекта (`AddDays`, `AddField`). `standard_calls: 0` — пересечение
   ничего не значит, решают вызовы с типом и код.
2. **Диспетчеризация** — `setup_candidates`, `kind: dispatch-interfaces`.
   `exclusivity` 1.0 и `sent` больше нуля — необходимое, а не достаточное:
   открой тип-аргумент у двух примеров. Запрос — свой класс-сообщение;
   коллекция (`ReadonlyList`), DTO клиентской библиотеки (`Content`),
   агент или канал (`AgentChannel<TAgent>`) — не диспетчеризация (squidex:
   `Content` 83 и `ReadonlyList` 67 выше `IMessageHandler` 23). 1.0 без
   отправок и без `handler_members` — обычно тестовая обвязка
   (`IClassFixture<…>`). В `dispatch_interfaces` пишется имя **без
   квалификатора** — строка «в ключ: …» ответа.
3. **Проверка 1–2**: кандидат стал `configured: true`, у записи охват
   больше нуля (`coverage` в `setup_status`: регистрации, обработчики).
   Человеку — число и пример, вопроса нет.
4. **Группы без решения** — кластеры `dotnet.undecided`, крупнейший
   первым. По кластеру — `setup_symbols` с `module` или `path`, `limit` 20
   (вместе с `path` поуже: кластер `module` на сотни типов сначала режь
   срезами `setup_stats`): что общего (база, атрибут, суффикс вместе
   с наследованием, каталог).
   - Признак вида доказуем кодом и вид уже объявлен в `rules.yaml` —
     правило пишешь сам, условие по признаку группы, а не перечнем FQN;
     доказательство — строкой `# доказательство: …` над `- id:` (пример
     ниже). Покажи: `dotnet.undecided` уменьшился на размер кластера,
     другие находки не выросли.
   - Иначе — вопрос «Типы .NET» по разделу `dotnet.undecided` каталога
     (`docs/setup-interview.md`): «как вид», «не документируем»,
     «не документируем, кроме» (`unless`), «новый вид».
5. **После правила** — `setup_explain` по каталогу группы: решение и
   правило-победитель. Отсев отменяет классификацию при любом приоритете:
   тип, попавший под широкое правило отсева, вид не получит. Нужна часть
   группы — `unless` у отсева, а не правило вида с большим приоритетом.
6. **`parse.errors`** — файл разобран с ошибками и не дал ни одного
   объявления, его типы пропали. Открой файл: чаще всего `#if` внутри
   выражения (аргумент атрибута, fluent-цепочка, базовый список). Код
   ты не правишь — вопрос «Разбор» по разделу `parse.errors`: `exclude`
   с причиной человека или правка исходника командой продукта.
7. **`require_public`** в секции `dotnet` — решение набора «внутреннюю
   кухню не документируем». Кода находки нет — скажи вслух: набор не
   документирует `internal`. Правка `dotnet.exclude.require_public` —
   только по слову человека.

## Контрольная точка

- `setup_status` — нет `dotnet.undecided` и `parse.errors`;
- `setup_stats` с `lang: cs` — без решения 0, документируем больше нуля;
- каждая запись `di_methods` и `dispatch_interfaces` — с охватом больше нуля.

## Ловушки

- **Стандартной формы DI может не быть вовсе.** На squidex без ключа —
  67 регистраций вместо 421: остальные идут через самодельные
  `AddSingletonAs` и `AddTransientAs`, и их ноль на фоне 67 незаметен.
  Лямбда-форма (`AddX(_ => new T())`) не станет регистрацией и с ключом —
  ограничение разбора, а не находки.
- **`MediatR.IRequestHandler` не совпадёт ни с чем**: сверяется голова
  прямой базы без пространства имён.
- **FQN — не уникальный ключ** (ABP: 255 коллизий): решение — условием
  по группе, а не списком имён; список сломается на первом переименовании.
- **Атрибуция — по `priority`, затем по `id`**, не по порядку в файле:
  равный приоритет двух правил вида решает меньший `id`, молча.
- **`name_regex` — полное совпадение**: `Dto` не ловит `OrderDto`, нужно `^.*Dto$`.
- **Многострочная база** (`EndpointBaseAsync.WithRequest<A>…`): значимо
  первое звено — `base_type: ["EndpointBaseAsync"]`.
- **`inherits` рвётся на файле под `exclude`** `docpipe.yaml`: правило
  молча не совпадает у наследников.
- **`#if` внутри выражения** ломает разбор целиком: тип исчезает, признак
  один — `parse.errors`.
- **`di_methods` входит в ключ кэша разбора**: правка сбрасывает кэш,
  следующий прогон дольше — так и задумано.
- **Слово «тест» бывает бизнес-понятием** (`StressTest`, `BackTest`):
  правило отсева `tests` набора ловит их по имени — проверь на кластере.

## Правка файлов

- `docpipe.yaml`: `di_methods`, `dispatch_interfaces` — раскомментируй
  ключ, запись — объект с `name` и `reason`.
- `rules.yaml`, секция `dotnet`: правило вида — в `dotnet.rules`, отсев —
  в `dotnet.exclude.rules`; у каждого правила свой `id`, не совпадающий
  с набором; `priority` у правила вида обязателен, а `reason` у него нет:
  лишний ключ — отказ загрузки всего набора, доказательство — строкой
  `# доказательство: …` над `- id:`. Новый вид — ещё и
  скелет `templates/<вид>.md` в КАТАЛОГ (фаза 50). После правки подними
  `ruleset_version` секции.

```yaml
# file: docpipe.yaml
di_methods:
  - name: "AddSingletonAs"
    reason: "setup candidates di-methods: 275 вызовов с типом, пример src/Startup.cs:40"
dispatch_interfaces:
  - name: "IRequestHandler"
    reason: "setup candidates dispatch-interfaces: исключительность 1.0, 4 отправки"
```

```yaml
# file: rules.yaml#dotnet
dotnet:
  rules:
    # доказательство: setup_symbols path src/Handlers — 12 из 12 наследуют IRequestHandler
    - id: handler.request
      kind: service
      template: service
      priority: 80
      when:
        inherits: ["IRequestHandler"]
  exclude:
    rules:
      - id: entry.program
        reason: "<слова человека>"
        when:
          name_regex: ["^Program$"]
      - id: grid.support
        reason: "<слова человека>"
        when:
          path_glob: ["src/Grid/**"]
        unless:
          inherits: ["GridService"]
```
