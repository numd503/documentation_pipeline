namespace Sample.Pricing.Api.Services;

public interface IPricingService
{
    Task<decimal> PriceAsync(Guid id, CancellationToken ct);
}
