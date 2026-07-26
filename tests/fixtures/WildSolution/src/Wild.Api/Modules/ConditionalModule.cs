using System;
using Wild.Api.Core;

#if EntityFrameworkCore
using Wild.Api.EntityFrameworkCore;
#elif MongoDB
using Wild.Api.MongoDB;
#endif

namespace Wild.Api.Modules;

// Конструкция из ABP (Volo.CmsKit.Web.Unified/CmsKitWebUnifiedModule.cs):
// директива препроцессора внутри списка аргументов атрибута. Разбор ломается,
// и объявление класса теряется целиком — см. docs/findings-abp.md.
[DependsOn(
    typeof(WildCoreModule),
    typeof(WildWebModule),
    typeof(WildHttpApiModule),
#if EntityFrameworkCore
    typeof(WildEntityFrameworkCoreModule),
    typeof(WildAuditLoggingEntityFrameworkCoreModule),
    typeof(WildSettingManagementEntityFrameworkCoreModule),
#elif MongoDB
    typeof(WildMongoDbModule),
    typeof(WildAuditLoggingMongoDbModule),
    typeof(WildSettingManagementMongoDbModule),
#endif
    typeof(WildAutofacModule),
    typeof(WildSwashbuckleModule)
)]
public class ConditionalModule : WildModule
{
    public override void ConfigureServices(ServiceConfigurationContext context)
    {
        var configuration = context.Services.GetConfiguration();

        context.Services.AddSingleton<IClock, SystemClock>();

#if EntityFrameworkCore
        context.Services.AddWildDbContext<WildDbContext>(options =>
        {
            options.AddDefaultRepositories();
        });
#endif

        Configure<WildOptions>(options =>
        {
            options.ConnectionString = configuration["ConnectionStrings:Default"];
        });
    }
}
