import { Routes } from '@angular/router';

import { ForecastComponent } from '../routes/forecast/forecast.component';

export const lazyRoutes: Routes = [
  // Короткая форма `loadComponent`: не `import().then()`, а уже импортированный класс.
  { path: 'daily', loadComponent: () => ForecastComponent },
];
