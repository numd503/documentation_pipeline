; Члены классов и интерфейсов. Как и на .NET, запрос только находит узлы:
; принадлежность члена конкретному типу вычисляется обходом ВВЕРХ, потому что
; tree-sitter подниматься по дереву не умеет.
;
; `method_definition` — и метод, и конструктор, и `get`/`set`: различаются они
; ключевым словом-потомком, а не типом узла. Искать конструктор по типу узла
; (как `constructor_declaration` в C#) здесь не по чему.

(method_definition) @member
(public_field_definition) @member

; Формы, которых у класса нет, а у интерфейса и абстрактного класса есть.
; `property_signature` покрывает и свойство интерфейса, и объявление метода
; через тип-функцию; `method_signature` — обычную форму метода интерфейса.
(property_signature) @member
(method_signature) @member
(abstract_method_signature) @member
