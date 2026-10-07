import { Injectable } from '@angular/core';
import { Action, Selector, State, StateContext } from '@ngxs/store';
import { tap } from 'rxjs/operators';

import { OrdersApiService } from '../api/orders-api.service';
import { Order } from '../api/order';
import { LoadOrders, SelectOrder } from './orders.actions';

export interface OrdersStateModel {
  items: Order[];
  selected: Order | null;
}

// Состояние раздела. Страницы доходят до него диспатчем экшена, а не
// внедрением, — и обе страницы, поэтому поглотить его не может ни одна.
@State<OrdersStateModel>({
  name: 'orders',
  defaults: { items: [], selected: null },
})
@Injectable()
export class OrdersState {
  constructor(private api: OrdersApiService) {}

  @Selector()
  static items(state: OrdersStateModel): Order[] {
    return state.items;
  }

  @Selector()
  static selected(state: OrdersStateModel): Order | null {
    return state.selected;
  }

  @Action(LoadOrders)
  load(ctx: StateContext<OrdersStateModel>) {
    return this.api.list().pipe(tap((items) => ctx.patchState({ items })));
  }

  @Action(SelectOrder)
  select(ctx: StateContext<OrdersStateModel>, { id }: SelectOrder) {
    return this.api.byId(id).pipe(tap((selected) => ctx.patchState({ selected })));
  }
}
