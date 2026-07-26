using Apache.Ignite.Core.Services;

namespace Sample.Pricing.Api.Grid;

/// <summary>Risk aggregation running on the compute grid.</summary>
public sealed class RiskComputeService : IService
{
    public void Init(IServiceContext context) { }
    public void Execute(IServiceContext context) { }
    public void Cancel(IServiceContext context) { }
}
