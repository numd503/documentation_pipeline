namespace Sample.Common.Abstractions;

/// <summary>Supplies pricing inputs.</summary>
public interface IPricingProvider<T> where T : class
{
    T Get(string key);
}
