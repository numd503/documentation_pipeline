; Директивы using. Запрос, а не обход детей `compilation_unit`: директива может
; лежать внутри блочного namespace (`namespace N { using X; … }`) — в ABP таких 8.
;
; Ложных срабатываний внутри методов не будет: `using var s = …` разбирается
; как `local_declaration_statement`, а `using (…) { }` — как `using_statement`.
; Ни то, ни другое узлом `using_directive` не является.

(using_directive) @using
