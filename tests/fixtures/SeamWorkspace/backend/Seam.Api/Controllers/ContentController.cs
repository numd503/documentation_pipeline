using Microsoft.AspNetCore.Mvc;
using Seam.Api.Web;

namespace Seam.Api.Controllers;

// Публичный API для внешних клиентов (SDK, интеграции): фронт его не зовёт,
// и «эндпоинт без вызывающего» здесь — не дефект, а решение «зовут извне».
// Свой `[Route]` у класса есть, поэтому маршрут базы `ApiController`
// не применяется: ASP.NET берёт первый класс иерархии, где маршрут объявлен.
[Route("content/{app}")]
public sealed class ContentController : ApiController
{
    [HttpGet("{schema}")]
    public IActionResult GetContents(string app, string schema) => Ok();

    [HttpGet("{schema}/{id}")]
    public IActionResult GetContent(string app, string schema, string id) => Ok();

    [HttpPost("{schema}")]
    public IActionResult PostContent(string app, string schema, [FromBody] object data) => Ok();
}
