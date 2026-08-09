using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;

namespace Wild.Api.Controllers;

/// <summary>
/// Формы маршрутизации, преобладающие в АС CF: 433 атрибута [Route], из них
/// с токеном [controller] — ноль, пустых [HttpGet] — 556 против 47 с аргументом.
/// </summary>
[ApiController]
[Route("api/ml/innerdebts")]
public class InnerDebtsController : ControllerBase
{
    /// <summary>Глагол и путь объявлены РАЗНЫМИ атрибутами.</summary>
    /// <remarks>
    /// Реализация, читающая только аргумент Http*, выдаст всем методам такого
    /// контроллера один и тот же маршрут — маршрут класса.
    /// </remarks>
    [HttpGet]
    [Route("state/byclient")]
    public Task<IActionResult> StateByClientAsync() => throw new System.NotImplementedException();

    /// <summary>Обычная форма: путь в аргументе самого атрибута.</summary>
    [HttpPost("insert")]
    public Task<IActionResult> InsertAsync() => throw new System.NotImplementedException();

    /// <summary>Абсолютный шаблон: маршрут класса отбрасывается целиком.</summary>
    [HttpGet("~/formsx/models/list")]
    public Task<IActionResult> FormsListAsync() => throw new System.NotImplementedException();

    /// <summary>Пустой [HttpGet] без [Route]: маршрут равен маршруту класса.</summary>
    [HttpGet]
    public Task<IActionResult> ListAsync() => throw new System.NotImplementedException();
}

/// <summary>
/// Контроллер конвенциональной маршрутизации: атрибутов нет вовсе, URL задаётся
/// в MapControllerRoute. Из атрибутов его маршрут не собирается, и вызовы фронта
/// к нему попали бы в «эндпоинт не найден» — то есть выглядели бы дефектом фронта.
/// </summary>
public class HomeController : Controller
{
    public IActionResult Index() => View();

    public IActionResult Privacy() => View();
}
