using Microsoft.AspNetCore.Mvc;

namespace Sample.Common.Web;

/// <summary>Base class for all API controllers.</summary>
[ApiController]
public abstract class BaseApiController : ControllerBase
{
    protected string TraceId => HttpContext.TraceIdentifier;
}
