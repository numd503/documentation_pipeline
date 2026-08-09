using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;

namespace Wild.Api.Controllers;

/// <summary>
/// Маршруты, которые зовёт фронт из фикстуры WebWorkspace. Нужны, чтобы связь
/// проверялась на настоящей паре манифестов, а не на выдуманных ключах.
/// </summary>
[ApiController]
[Route("api/ml/structure")]
public class MlStructureController : ControllerBase
{
    /// <summary>Точное совпадение с литералом фронта.</summary>
    [HttpGet]
    public Task<IActionResult> ListAsync() => throw new System.NotImplementedException();

    /// <summary>
    /// Пара «почти совпало»: фронт зовёт этот же маршрут с параметром на конце,
    /// то есть ключи различаются только числом подстановок. Это инвентарь,
    /// а не дефект: одна сторона знает про параметр, вторая нет.
    /// </summary>
    [HttpGet("getForUpdate")]
    public Task<IActionResult> GetForUpdateAsync() => throw new System.NotImplementedException();

    /// <summary>Эндпоинт без вызывающего: его может звать другая система.</summary>
    [HttpDelete("purge")]
    public Task<IActionResult> PurgeAsync() => throw new System.NotImplementedException();
}

/// <summary>
/// Коллизия маршрутов: тот же глагол и тот же путь, что у метода выше.
/// Единственная категория отчёта связи, которая является дефектом, — приложение
/// с такой парой падает на старте либо отвечает произвольным из двух действий.
/// </summary>
[ApiController]
[Route("api/ml/structure")]
public class MlStructureLegacyController : ControllerBase
{
    [HttpGet]
    public Task<IActionResult> ListAsync() => throw new System.NotImplementedException();
}
