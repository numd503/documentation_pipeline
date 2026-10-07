import { Routes } from '@angular/router';

import { OrdersDetailComponent } from './orders-detail/orders-detail.component';
import { OrdersListComponent } from './orders-list/orders-list.component';

// Две страницы одного раздела. Сам раздел (`shared-orders/`) маршрута не имеет:
// его состояние и сервисы открываются с обеих страниц, и по графу он общий.
export const routes: Routes = [
  { path: 'orders', component: OrdersListComponent },
  { path: 'orders/:id', component: OrdersDetailComponent },
  { path: '', redirectTo: 'orders', pathMatch: 'full' },
];
