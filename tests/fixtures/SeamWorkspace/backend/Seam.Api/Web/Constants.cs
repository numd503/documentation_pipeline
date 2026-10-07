namespace Seam.Api.Web;

public static class Constants
{
    // Префикс маршрута — константа, а не литерал: в `[Route]` базы попадает
    // выражение `Constants.PrefixApi`, и разбор атрибутов видит его сырым текстом.
    public const string PrefixApi = "api";
}
