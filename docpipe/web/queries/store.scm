; Цепочка NGXS: диспатч экшена, выборка из стейта, тип экшена.
;
; Отдельный файл, а не расширение `usage.scm`: обращение к стейту устроено
; иначе, чем обращение к сервису. Получателем там `Store` — тип внешний,
; ребра к нему нет и быть не может, а смысл несёт аргумент.

(call_expression
  function: (member_expression)
  arguments: (arguments)) @call

; `@Select(DebtState.items)` — декоратор поля.
(decorator) @decorator

; `static readonly type = '[Inner Debt] Load'` — единственное стабильное
; литеральное звено цепочки: имя класса меняется при рефакторинге, строка нет.
(public_field_definition) @field
