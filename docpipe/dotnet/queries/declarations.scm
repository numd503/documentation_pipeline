; Объявления типов — и на верхнем уровне, и вложенные.
;
; Запрос находит только сами узлы объявлений; имя, модификаторы, базовые типы
; и атрибуты извлекаются обходом потомков, а namespace и containing_type —
; обходом предков. Выразить это запросом нельзя: tree-sitter не умеет
; подниматься вверх по дереву.

(class_declaration) @declaration
(interface_declaration) @declaration
(struct_declaration) @declaration
(record_declaration) @declaration
(enum_declaration) @declaration
