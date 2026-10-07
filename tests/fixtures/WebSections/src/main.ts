import { provideHttpClient } from '@angular/common/http';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { provideStore } from '@ngxs/store';

import { AppComponent } from './app/app.component';
import { routes } from './app/app.routes';
import { OrdersState } from './app/shared-orders/state/orders.state';

bootstrapApplication(AppComponent, {
  providers: [provideRouter(routes), provideHttpClient(), provideStore([OrdersState])],
}).catch((err) => console.error(err));
