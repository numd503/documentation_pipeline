using Sample.Common.Abstractions;

namespace Sample.Pricing.Api.Providers;

/// <summary>Provides discount curves.</summary>
public sealed class CurveProvider : IPricingProvider<string>
{
    public string Get(string key) => key;
}
