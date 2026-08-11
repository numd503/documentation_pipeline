; Вызовы с получателем: `this.http.get(…)`, `_http.post(…)`.
;
; Отбор по `function: (member_expression)` отсекает вызовы без получателя,
; но этого мало: сам получатель проверяется в Python. Счётчик `.get(`/`.post(`
; без получателя меряет `Map.get`, `form.get('search')`, `headers.get(…)`
; и `queryParams.get(…)` — на боевом модуле таких 327 против 79 настоящих,
; и посчитанная по ним доля литералов выглядит убедительно, ничего не значая.

(call_expression
  function: (member_expression)) @call

; Литеральные инициализаторы, из которых восстанавливаются базы URL.
; Без них отчёт назовёт невосстановленными 27 % вызовов там, где настоящая
; дыра — единицы, и доверие к числу пропадёт раньше, чем его починят.

(lexical_declaration
  (variable_declarator
    name: (identifier)
    value: (_)) @constant)

(public_field_definition) @field
