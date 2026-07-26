namespace Wild.Api.Services;

// Перегрузка типа по числу параметров-дженериков: в C# это разные типы,
// но у них совпадают и простое имя, и FQN. В ABP таких групп 112, вплоть до
// шести арностей у одного имени. Ключ символа обязан включать арность,
// иначе разные типы склеятся в один узел документации — см. docs/findings-abp.md.

/// <summary>Read-only CRUD service.</summary>
public interface ICrudAppService<TEntityDto>
{
    Task<TEntityDto> GetAsync(Guid id);
}

/// <summary>CRUD service with a separate list DTO.</summary>
public interface ICrudAppService<TEntityDto, TListDto>
{
    Task<TListDto> GetListAsync();
}

/// <summary>CRUD service with a create input.</summary>
public interface ICrudAppService<TEntityDto, TListDto, TCreateInput>
{
    Task<TEntityDto> CreateAsync(TCreateInput input);
}
