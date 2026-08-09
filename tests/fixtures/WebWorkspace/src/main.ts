import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { provideStore } from '@ngxs/store';

import { appRoutes } from './app/app.routes';
import { authInterceptor } from './app/cf-api/interceptors/auth.interceptor';
import { DebtState } from './app/inner-debt/state/debt.state';
import { ShellComponent } from './app/routes/shell.component';

// Точка входа современного Angular — top-level statements, без класса и метода.
// Тот же случай, что `Program.cs` на .NET: поиск регистраций обязан идти
// по всему дереву файла, а не внутри объявления.
bootstrapApplication(ShellComponent, {
  providers: [
    provideRouter(appRoutes),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideStore([DebtState]),
  ],
}).catch((err) => console.error(err));
