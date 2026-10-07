using Microsoft.AspNetCore.Mvc;

namespace Seam.Api.Controllers;

// Настоящий конвенциональный контроллер: ни у класса, ни у методов нет
// атрибутов маршрута, URL задан шаблоном `MapControllerRoute` в `Program.cs`.
public sealed class LegacyController : Controller
{
    public IActionResult Index() => View();

    public IActionResult Export(int id) => File(Array.Empty<byte>(), "text/csv", $"export-{id}.csv");
}
