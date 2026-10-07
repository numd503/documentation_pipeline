using Microsoft.AspNetCore.Mvc;

namespace Seam.Api.Controllers;

// Действие с `[Route]` без глагола: ASP.NET принимает на нём любой HTTP-метод.
// Разбор, ищущий только `Http*`, не даёт такому действию эндпоинта вовсе,
// и контроллер попадает в «конвенциональные» (squidex: 6 таких действий).
[ApiController]
[Route("api")]
public sealed class CommentsController : ControllerBase
{
    [Route("comments/{id}")]
    public IActionResult GetComments(string id) => Ok(id);
}
