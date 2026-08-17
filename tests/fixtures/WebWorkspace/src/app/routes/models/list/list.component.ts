import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Select, Store } from '@ngxs/store';
import { Observable } from 'rxjs';

import { AuditService } from '@shared/services/audit.service';

import { DebtState } from '../../../inner-debt/state/debt.state';
import {
  LoadInnerDebts,
  ResetInnerDebts,
  SaveInnerDebt,
} from '../../../inner-debt/state/debt.actions';

@Component({
  selector: 'app-models-list',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: './list.component.html',
  styleUrls: ['./list.component.scss'],
})
export class ListComponent {
  // Выборка декоратором. Вторая форма — `store.selectSnapshot(…)` в `total()`;
  // обе означают одно и то же: страница смотрит в этот стейт.
  @Select(DebtState.items) items$!: Observable<unknown[]>;

  readonly form = new FormGroup({ search: new FormControl('') });

  private readonly cache = new Map<string, unknown>();

  constructor(
    private store: Store,
    private audit: AuditService,
  ) {}

  load(clientId: string): void {
    // Компонент не зовёт сервис — он диспатчит экшен. Без прохода по цепочке
    // «экшен → стейт → сервис» у страницы не будет связи с эндпоинтом.
    this.store.dispatch(new LoadInnerDebts(clientId));
    this.audit.log({ clientId });
  }

  save(payload: unknown): void {
    // Массив в `dispatch` — одна форма для разбора и два ребра. Реализация,
    // берущая первый аргумент как объект, на массиве даёт ноль, а именно так
    // пишут пакетную загрузку экрана.
    this.store.dispatch([new SaveInnerDebt(payload), new ResetInnerDebts()]);
  }

  total(): unknown {
    return this.store.selectSnapshot(DebtState.items);
  }

  cached(id: string): unknown {
    // Приманки для счётчика вызовов: ни `Map.get`, ни `FormGroup.get`
    // не являются HTTP-вызовами. На боевом модуле таких 327 против 79 настоящих,
    // и посчитанная по ним доля литералов выглядит убедительно, ничего не значая.
    const hit = this.cache.get(id);
    return hit ?? this.form.get('search')?.value;
  }
}
