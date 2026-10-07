import { Routes } from '@angular/router';

import { AppsPageComponent } from './pages/apps-page.component';

export const routes: Routes = [
  { path: 'apps', component: AppsPageComponent },
  { path: '', redirectTo: 'apps', pathMatch: 'full' },
];
