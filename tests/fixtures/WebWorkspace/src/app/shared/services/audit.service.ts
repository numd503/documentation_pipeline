import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Константа уровня модуля. В боевом модуле через такую же идут десять вызовов
// одного сервиса: без разрешения инициализатора-литерала отчёт назовёт их
// невосстановленными, и доверие к числу пропадёт раньше, чем его починят.
export const auditUrl = '/integration/log/AuditJ';

@Injectable({ providedIn: 'root' })
export class AuditService {
  constructor(private http: HttpClient) {}

  log(payload: unknown): Observable<void> {
    return this.http.post<void>(auditUrl, payload);
  }

  list(): Observable<unknown[]> {
    return this.http.get<unknown[]>(auditUrl);
  }

  purge(): Observable<void> {
    return this.http.delete<void>(auditUrl);
  }
}
