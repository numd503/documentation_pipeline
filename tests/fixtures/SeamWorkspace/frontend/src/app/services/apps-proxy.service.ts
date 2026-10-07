import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { RestService } from '@app/framework/rest.service';

// Обёртка с объектом-запросом — форма сгенерированных прокси abp.
// Получатель `rest` не похож на HttpClient, адрес лежит в поле `url`
// первого аргумента: без объявления обёртки вызов не виден вовсе.
@Injectable({ providedIn: 'root' })
export class AppsProxyService {
  constructor(private rest: RestService) {}

  create(body: unknown): Observable<unknown> {
    return this.rest.request<unknown>({ method: 'POST', url: '/api/apps', body });
  }
}
