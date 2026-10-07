import { Component, OnInit } from '@angular/core';
import { Select, Store } from '@ngxs/store';
import { Observable } from 'rxjs';

import { Order } from '@app/shared-orders/api/order';
import { OrdersApiService } from '@app/shared-orders/api/orders-api.service';
import { LoadOrders } from '@app/shared-orders/state/orders.actions';
import { OrdersState } from '@app/shared-orders/state/orders.state';

// Первая страница раздела: данные — диспатчем через стейт, выгрузка —
// прямым вызовом сервиса раздела.
@Component({
  selector: 'app-orders-list',
  standalone: true,
  template: '<button (click)="exportCsv()">CSV</button>',
})
export class OrdersListComponent implements OnInit {
  @Select(OrdersState.items) orders$!: Observable<Order[]>;

  constructor(
    private store: Store,
    private api: OrdersApiService,
  ) {}

  ngOnInit(): void {
    this.store.dispatch(new LoadOrders());
  }

  exportCsv(): void {
    this.api.exportCsv().subscribe();
  }
}
