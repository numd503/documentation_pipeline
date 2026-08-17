import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Импорт через алиас `@shared` без подстановки: путь ведёт в каталог,
// оттуда — в `index.ts`, оттуда — в переэкспорт. Три звена подряд.
import { AuditService } from '@shared/services/audit.service';
import { BaseApiService } from '@shared';

@Injectable({ providedIn: 'root' })
export class InnerDebtService extends BaseApiService {
  constructor(http: HttpClient, audit: AuditService) {
    super(http, audit);
  }

  byClient(clientId: string): Observable<unknown> {
    return this.http.get(`api/ml/innerdebts/state/byclient/${clientId}`);
  }

  insert(payload: unknown): Observable<void> {
    // Получатель объявлен в базовом классе: ребро возникает только через
    // унаследованное связывание.
    this.audit.log(payload);
    return this.http.post<void>('api/ml/innerdebts/insert', payload);
  }
}
