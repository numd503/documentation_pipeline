using Microsoft.AspNetCore.Mvc;

namespace Seam.Api.Controllers;

// Несколько глаголов одним атрибутом: эндпоинт на каждый глагол.
[ApiController]
[Route("api/verbs")]
public sealed class VerbsController : ControllerBase
{
    [AcceptVerbs("GET", "POST")]
    public IActionResult Echo() => Ok();
}
