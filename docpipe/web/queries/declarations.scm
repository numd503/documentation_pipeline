; Объявления TypeScript, из которых получаются символы.
;
; Классы, интерфейсы и перечисления берутся независимо от `export`: в Angular
; встречаются и неэкспортируемые классы с декоратором (компонент, объявленный
; в `@NgModule` того же файла), и терять их нельзя — по ним тоже есть решение,
; пусть даже «не документируем».
;
; Функции и константы берутся ТОЛЬКО экспортируемые, и выражено это структурой
; запроса, а не проверкой в Python. Иначе `lexical_declaration` поймал бы каждое
; `const` внутри каждого тела метода — это тысячи узлов на модуль, и все они
; не объявления, а локальные переменные.

(class_declaration) @declaration
(abstract_class_declaration) @declaration
(interface_declaration) @declaration
(enum_declaration) @declaration

(export_statement declaration: (function_declaration) @declaration)
(export_statement declaration: (lexical_declaration) @declaration)
