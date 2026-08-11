import { Injectable } from '@angular/core';
import { Action, Selector, State, StateContext } from '@ngxs/store';
import { tap } from 'rxjs/operators';

import { InnerDebtService } from '../services/inner-debt.service';
import { LoadInnerDebts, SaveInnerDebt } from './debt.actions';

export interface DebtStateModel {
  items: unknown[];
  loading: boolean;
}

// Декоратор стейта всегда написан с дженериком, поэтому поиск по подстроке
// без него даёт ноль при существующих стейтах, и молча. Декораторов у класса
// два, и собрать обязательно оба.
@State<DebtStateModel>({
  name: 'innerDebt',
  defaults: { items: [], loading: false },
})
@Injectable()
export class DebtState {
  constructor(private service: InnerDebtService) {}

  @Selector()
  static items(state: DebtStateModel): unknown[] {
    return state.items;
  }

  @Action(LoadInnerDebts)
  load(ctx: StateContext<DebtStateModel>, { clientId }: LoadInnerDebts) {
    return this.service
      .byClient(clientId)
      .pipe(tap((items) => ctx.patchState({ items: items as unknown[] })));
  }

  @Action(SaveInnerDebt)
  save(ctx: StateContext<DebtStateModel>, { payload }: SaveInnerDebt) {
    return this.service.insert(payload);
  }
}
