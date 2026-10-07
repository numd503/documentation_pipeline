using Microsoft.AspNetCore.Mvc;
using Seam.Api.Web;

namespace Seam.Api.Controllers;

// Контроллер на абстрактной базе: своего `[Route]` нет, префикс `api`
// приходит от `ApiController`. Без наследования маршрута эндпоинты выглядят
// как `GET apps`, и ни один вызов фронта `api/apps` к ним не ложится.
public sealed class AppsController : ApiController
{
    [HttpGet("apps")]
    public IActionResult GetApps() => Ok(Array.Empty<object>());

    [HttpGet("apps/{app}")]
    public IActionResult GetApp(string app) => Ok(new { app });

    [HttpPost("apps")]
    public IActionResult PostApp([FromBody] object request) => Created("apps", request);

    // Адресат `HTTP.requestVersioned(this.http, 'PUT', url)` фронта.
    [HttpPut("apps/{app}")]
    public IActionResult PutApp(string app, [FromBody] object request) => Ok(request);
}
