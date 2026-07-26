using Microsoft.Extensions.DependencyInjection;
using Sample.Common.Abstractions;
using Sample.Pricing.Api.Providers;
using Sample.Pricing.Api.Services;

namespace Sample.Pricing.Api;

public static class Program
{
    public static void ConfigureServices(IServiceCollection services)
    {
        services.AddScoped<IPricingService, PricingService>();
        services.AddSingleton<IPricingProvider<string>, CurveProvider>();
        services.AddTransient<ValuationWorkflow>();
    }
}
