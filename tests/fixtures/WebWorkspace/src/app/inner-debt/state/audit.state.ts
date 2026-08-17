import { Injectable } from '@angular/core';
import { Action, State, StateContext } from '@ngxs/store';

import { AuditService } from '@shared/services/audit.service';
import { SaveInnerDebt } from './debt.actions';

export interface AuditStateModel {
  written: number;
}

// ВТОРОЙ стейт, обрабатывающий тот же экшен `SaveInnerDebt`. В NGXS это
// законно и встречается: сохранение пишет данные в одном стейте и журнал
// в другом. Реализация, берущая первый попавшийся обработчик, потеряет
// половину состояния страницы молча.
@State<AuditStateModel>({
  name: 'innerDebtAudit',
  defaults: { written: 0 },
})
@Injectable()
export class AuditState {
  constructor(private audit: AuditService) {}

  @Action(SaveInnerDebt)
  write(ctx: StateContext<AuditStateModel>, { payload }: SaveInnerDebt) {
    ctx.patchState({ written: ctx.getState().written + 1 });
    return this.audit.log(payload);
  }
}
