using Microsoft.AspNetCore.Mvc;
using Seam.Api.Web;

namespace Seam.Api.Controllers;

// Наследник базы с токеном: маршрут собирается как `api/[controller]` → `api/Orders`.
public sealed class OrdersController : TokenApiController
{
    [HttpGet]
    public IActionResult GetOrders() => Ok(Array.Empty<object>());

    [HttpGet("{id}")]
    public IActionResult GetOrder(int id) => Ok(id);

    // Тот же относительный маршрут, что у `InfoController`, — см. там.
    [HttpGet("info")]
    public IActionResult GetInfo() => Ok(new { total = 0 });
}
