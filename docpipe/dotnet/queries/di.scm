; Вызовы вида `services.AddScoped<IFoo, Foo>()`.
;
; Запрос по всему дереву файла, а не внутри `method_declaration`: в современном
; .NET `Program.cs` пишется через top-level statements, и регистрации лежат прямо
; в `global_statement`, без класса и метода вообще. В eShopOnWeb так написаны
; 14 регистраций из 37.
;
; Отбор по `function: (member_access_expression)` отсекает вызовы без получателя
; (`Foo()`), которых в файле подавляющее большинство. Имя метода проверяется
; в Python: предикаты запроса тут не помогут — нужен разбор суффикса на lifetime.

(invocation_expression
  function: (member_access_expression)) @call
