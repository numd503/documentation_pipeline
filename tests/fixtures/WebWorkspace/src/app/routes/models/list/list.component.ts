import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Store } from '@ngxs/store';

import { AuditService } from '@shared/services/audit.service';

import { LoadInnerDebts } from '../../../inner-debt/state/debt.actions';

@Component({
  selector: 'app-models-list',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: './list.component.html',
  styleUrls: ['./list.component.scss'],
})
export class ListComponent {
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

  cached(id: string): unknown {
    // Приманки для счётчика вызовов: ни `Map.get`, ни `FormGroup.get`
    // не являются HTTP-вызовами. На боевом модуле таких 327 против 79 настоящих,
    // и посчитанная по ним доля литералов выглядит убедительно, ничего не значая.
    const hit = this.cache.get(id);
    return hit ?? this.form.get('search')?.value;
  }
}
