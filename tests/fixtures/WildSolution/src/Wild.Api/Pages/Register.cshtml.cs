namespace Wild.Api.Pages;

/// <summary>Registration page model.</summary>
public sealed class RegisterModel
{
    public InputModel Input { get; set; } = new();

    /// <summary>Same simple name as LoginModel.InputModel, different containing type.</summary>
    public sealed class InputModel
    {
        public string Password { get; set; } = string.Empty;
    }
}
