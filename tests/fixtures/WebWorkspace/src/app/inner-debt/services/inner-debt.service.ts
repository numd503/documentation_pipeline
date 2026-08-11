import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Импорт через алиас `@shared` без подстановки: путь ведёт в каталог,
// оттуда — в `index.ts`, оттуда — в переэкспорт. Три звена подряд.
import { BaseApiService } from '@shared';

@Injectable({ providedIn: 'root' })
export class InnerDebtService extends BaseApiService {
  constructor(http: HttpClient) {
    super(http);
  }

  byClient(clientId: string): Observable<unknown> {
    return this.http.get(`api/ml/innerdebts/state/byclient/${clientId}`);
  }

  insert(payload: unknown): Observable<void> {
    return this.http.post<void>('api/ml/innerdebts/insert', payload);
  }
}
