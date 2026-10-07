using Microsoft.AspNetCore.Mvc;
using Seam.Api.Web;

namespace Seam.Api.Controllers;

// Адресат прямого вызова фронта `this.http.get('api/info')`.
// Относительный маршрут `info` есть и у `OrdersController` под другой базой:
// без наследования `[Route]` оба дают `GET info` — дубль, которого нет
// (squidex: единственный «дефект» `web link`).
public sealed class InfoController : ApiController
{
    [HttpGet("info")]
    public IActionResult GetInfo() => Ok(new { version = "1.0" });
}
