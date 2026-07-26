using Microsoft.Extensions.DependencyInjection;
using Wild.Api.Endpoints;

// Top-level statements: ни namespace, ни класса, ни метода.
// Регистрации DI лежат прямо на уровне файла — реализация, которая ищет
// их внутри method_declaration, не найдёт здесь ничего.
var builder = WebApplication.CreateBuilder(args);

builder.Services.AddScoped<IAuthenticateService, AuthenticateService>();
builder.Services.AddSingleton<ICatalogReader, CatalogReader>();
builder.Services.TryAddTransient<IClock, SystemClock>();
builder.Services.AddHostedService<CatalogWarmupWorker>();

var app = builder.Build();

app.Run();
