using Microsoft.AspNetCore.Mvc;

namespace Seam.Api.Web;

// Абстрактная база контроллеров API — форма squidex (42 контроллера из 48).
// ASP.NET Core ищет `[Route]` вверх по иерархии до первого класса, где он
// объявлен, поэтому у наследника без своего маршрута URL начинается с `api/`.
// Своих действий у базы нет, и «контроллером без маршрутов» она не является.
[ApiController]
[Route(Constants.PrefixApi)]
public abstract class ApiController : ControllerBase
{
    protected string UserId => User.Identity?.Name ?? string.Empty;
}
