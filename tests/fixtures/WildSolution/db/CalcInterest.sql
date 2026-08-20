-- Процедура из дикой природы: читает две таблицы, пишет в третью,
-- зовёт соседнюю процедуру и использует временную таблицу.
--
-- Временная таблица здесь не для красоты: без правила «временные узлами
-- данных не становятся» граф зарастает `#tmp`, а веерность теряет смысл —
-- у `#tmp` одной процедуры нет ничего общего с `#tmp` другой.
CREATE PROCEDURE dbo.CalcInterest
    @ContractId int
AS
BEGIN
    CREATE TABLE #daily (Day date, Amount decimal(18,2));

    INSERT INTO #daily (Day, Amount)
    SELECT s.Day, s.Amount
    FROM [dbo].[CONTRACT_SCHEDULE] s
    JOIN dbo.RATES r ON r.Id = s.RateId
    WHERE s.ContractId = @ContractId;

    UPDATE dbo.CONTRACTS
    SET InterestAmount = (SELECT SUM(Amount) FROM #daily)
    WHERE Id = @ContractId;

    EXEC dbo.WriteAudit @ContractId;
END
