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

  // Вызов внутри локальной функции внутри метода. Диапазон метода накрывает
  // стрелку целиком, поэтому владельцем обязан стать САМЫЙ УЗКИЙ накрывающий
  // член — сам `retry`. Перебор членов в порядке объявления вернул бы первый
  // попавшийся накрывающий, и вызовы уехали бы в чужой метод.
  retry(payload: unknown): Observable<void> {
    const send = () => this.http.post<void>(auditUrl, payload);
    return send();
  }

  // Стрелка в поле класса — тоже член, и однострочный: сравнение границ
  // обязано быть нестрогим с обеих сторон, иначе такой вызов останется ничьим.
  readonly ping = (): Observable<unknown> => this.http.get(auditUrl);
}

// Вызов вне какого-либо члена: фабрика уровня модуля. `member` здесь пустой,
// и это состояние «записан вне члена», а не «член неизвестен».
export const pingAudit = (http: HttpClient): Observable<unknown> => http.get(auditUrl);
