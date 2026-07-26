using Sample.Common.Abstractions;

namespace Sample.Pricing.Api.Services;

/// <summary>Computes prices for instruments.</summary>
public sealed partial class PricingService : IPricingService
{
    private readonly IPricingProvider<string> _curves;

    public PricingService(IPricingProvider<string> curves)
    {
        _curves = curves;
    }

    public Task<decimal> PriceAsync(Guid id, CancellationToken ct)
    {
        return Task.FromResult(Discount(1m));
    }
}
