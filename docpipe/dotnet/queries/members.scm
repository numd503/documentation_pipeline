; Члены типов. Как и в declarations.scm, запрос только находит узлы:
; принадлежность члена конкретному типу вычисляется в Python обходом вверх,
; потому что tree-sitter не умеет подниматься по дереву.
;
; Запрос, а не обход детей тела: члены под `#if` прямыми детьми `declaration_list`
; не являются, они уходят под `preproc_if` / `preproc_else`. Запрос находит их
; на любой глубине, и молчаливой потери не происходит.

(method_declaration) @member
(property_declaration) @member
(field_declaration) @member
(constructor_declaration) @member

; Событие объявляется двумя разными узлами. `event_field_declaration` —
; обычная форма (`public event EventHandler Changed;`), `event_declaration` —
; форма с add/remove. Первая встречается вдвое чаще; забыть её легко,
; потому что в имени узла нет слова, по которому её станешь искать.
(event_field_declaration) @member
(event_declaration) @member
