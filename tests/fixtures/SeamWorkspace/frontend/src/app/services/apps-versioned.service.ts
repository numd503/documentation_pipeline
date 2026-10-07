import { HttpClient, HttpResponse } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { HTTP } from '@app/framework/http-extensions';

// Вызовы через обёртки `HTTP.*Versioned` (объявлены в `framework/http-extensions.ts`).
// Получатель — `HTTP`, а не `this.http`, и глагола HttpClient в имени нет:
// без объявления обёртки разбор не видит эти вызовы вовсе.
@Injectable({ providedIn: 'root' })
export class AppsVersionedService {
  constructor(private http: HttpClient) {}

  // Адрес — второй позиционный аргумент; первый — сам HttpClient.
  getApps(): Observable<HttpResponse<unknown>> {
    const url = 'api/apps';
    return HTTP.getVersioned(this.http, url);
  }

  // Метод — из аргумента: второй аргумент — метод, третий — адрес.
  putApp(app: string, dto: unknown, version: string): Observable<HttpResponse<unknown>> {
    return HTTP.requestVersioned(this.http, 'PUT', `api/apps/${app}`, version, dto);
  }
}
