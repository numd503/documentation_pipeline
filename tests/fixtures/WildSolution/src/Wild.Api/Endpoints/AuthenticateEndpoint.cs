using Wild.Api.Contracts;

namespace Wild.Api.Endpoints;

/// <summary>Issues an access token for valid credentials.</summary>
public sealed class AuthenticateEndpoint : EndpointBaseAsync
    .WithRequest<AuthenticateRequest>
    .WithActionResult<AuthenticateResponse>
{
    public override Task<ActionResult<AuthenticateResponse>> HandleAsync(
        AuthenticateRequest request,
        CancellationToken ct = default) => throw new NotImplementedException();
}
