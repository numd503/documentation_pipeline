-- Динамический SQL: имя таблицы собирается строкой и исполняется.
-- Разрешить это нельзя ничем — имени нет вовсе до момента исполнения,
-- и категория отчёта у него отдельная: это не «не разобрали литерал».
CREATE PROCEDURE dbo.BuildReport
    @Table sysname
AS
BEGIN
    DECLARE @sql nvarchar(max) = N'SELECT * FROM ' + QUOTENAME(@Table);
    EXEC (@sql);
END
