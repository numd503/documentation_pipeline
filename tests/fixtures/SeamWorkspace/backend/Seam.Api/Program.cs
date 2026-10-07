// Top-level statements: класса и метода нет, как в любом современном .NET.
var builder = WebApplication.CreateBuilder(args);
builder.Services.AddControllers();

var app = builder.Build();

app.MapControllers();

// Конвенциональная маршрутизация: URL `LegacyController` задан этим шаблоном,
// а не атрибутами на типе. Разбор атрибутов его не видит (АС CF — одно такое
// место, squidex — ноль, abp — три).
app.MapControllerRoute(
    name: "legacy",
    pattern: "legacy/{action=Index}/{id?}",
    defaults: new { controller = "Legacy" });

// Minimal API: эндпоинт без контроллера вовсе.
app.MapGet("/health", () => Results.Ok("healthy"));

app.Run();
