namespace Wild.Api.Pages;

/// <summary>Login page model.</summary>
public sealed class LoginModel
{
    public InputModel Input { get; set; } = new();

    /// <summary>Nested type whose simple name is not unique in the project.</summary>
    public sealed class InputModel
    {
        public string Email { get; set; } = string.Empty;
    }
}
