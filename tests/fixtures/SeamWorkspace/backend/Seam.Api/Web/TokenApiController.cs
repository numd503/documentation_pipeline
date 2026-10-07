using Microsoft.AspNetCore.Mvc;

namespace Seam.Api.Web;

// База с токеном: `[controller]` подставляется именем НАСЛЕДНИКА, а не базы.
// У `OrdersController` маршрут — `api/Orders`, а не `api/TokenApi`.
[ApiController]
[Route("api/[controller]")]
public abstract class TokenApiController : ControllerBase
{
}
