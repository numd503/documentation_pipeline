import { Component, OnInit } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { Store } from '@ngxs/store';
import { Observable } from 'rxjs';

import { Order } from '@app/shared-orders/api/order';
import { SelectOrder } from '@app/shared-orders/state/orders.actions';
import { OrdersState } from '@app/shared-orders/state/orders.state';

import { OrderEvent, OrderHistoryService } from './order-history.service';

// Вторая страница раздела: тот же стейт (диспатч и `store.select`) плюс свой
// приватный сервис рядом с компонентом.
@Component({
  selector: 'app-orders-detail',
  standalone: true,
  template: '<h1>{{ id }}</h1>',
})
export class OrdersDetailComponent implements OnInit {
  id = '';
  order$!: Observable<Order | null>;
  events: OrderEvent[] = [];

  constructor(
    private route: ActivatedRoute,
    private store: Store,
    private history: OrderHistoryService,
  ) {}

  ngOnInit(): void {
    // `paramMap.get` — не HTTP-вызов: счётчик без получателя посчитал бы его.
    this.id = this.route.snapshot.paramMap.get('id') ?? '';
    this.order$ = this.store.select(OrdersState.selected);
    this.store.dispatch(new SelectOrder(this.id));
    this.history.byOrder(this.id).subscribe((events) => (this.events = events));
  }
}
