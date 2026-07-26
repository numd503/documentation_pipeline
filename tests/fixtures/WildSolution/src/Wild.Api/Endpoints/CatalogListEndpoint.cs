namespace Wild.Api.Endpoints;

/// <summary>Returns the paged catalog listing.</summary>
public sealed class CatalogListEndpoint : IEndpoint<IResult, ListRequest, IRepository<CatalogItem>>
{
    public void AddRoute(IEndpointRouteBuilder app) { }
}
