using Microsoft.AspNetCore.Mvc;
using Sample.Common.Web;
using Sample.Pricing.Api.Services;

namespace Sample.Pricing.Api.Controllers;

/// <summary>Handles pricing requests.</summary>
[Route("api/v1/[controller]")]
public sealed class PricingController : BaseApiController
{
    private readonly IPricingService _pricing;

    public PricingController(IPricingService pricing)
    {
        _pricing = pricing;
    }

    [HttpGet("{id:guid}")]
    public async Task<ActionResult<decimal>> GetAsync(Guid id, CancellationToken ct)
    {
        return await _pricing.PriceAsync(id, ct);
    }

    [HttpPost]
    public Task<ActionResult> RecalculateAsync() => Task.FromResult<ActionResult>(Ok());
}
