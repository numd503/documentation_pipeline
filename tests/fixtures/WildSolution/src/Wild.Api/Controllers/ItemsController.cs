using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;

namespace Wild.Api.Controllers;

/// <summary>
/// Универсальный эндпоинт списков платформы: один маршрут на много смыслов.
/// Какой именно список запрошен, говорит не маршрут, а параметр `listInnerName`
/// — в теле у `query` и в query-строке у `GET`. Ключ «метод + маршрут» склеил бы
/// обращения к пользователям, к моделям и к справочникам в одну точку.
/// </summary>
[ApiController]
[Route("api/items")]
public class ItemsController : ControllerBase
{
    [HttpPost("query")]
    public Task<IActionResult> QueryAsync() => throw new System.NotImplementedException();

    [HttpGet]
    public Task<IActionResult> ListAsync() => throw new System.NotImplementedException();
}
