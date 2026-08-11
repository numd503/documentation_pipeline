; Импорты и переэкспорты.
;
; Запрос, а не обход детей `program`: `import()` внутри выражения — это
; `call_expression`, а не `import_statement`, и в захват не попадёт; зато
; `import` внутри блока `declare module { … }` попадёт, а он там законен.
;
; Переэкспорт отбирается по НАЛИЧИЮ поля `source`: `export { A }` без `from` —
; это переэкспорт внутри файла, а не связь с другим модулем, и в резолве
; ему делать нечего.

(import_statement) @import
(export_statement source: (string)) @re_export
