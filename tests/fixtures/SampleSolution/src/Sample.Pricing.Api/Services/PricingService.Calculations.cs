namespace Sample.Pricing.Api.Services;

public sealed partial class PricingService
{
    private static decimal Discount(decimal value) => value * 0.98m;
}
