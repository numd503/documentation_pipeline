import { HttpClient, HttpHeaders, HttpResponse } from '@angular/common/http';
import { Observable } from 'rxjs';

// Обёртки над HttpClient с версией ресурса — форма squidex (82 вызова в 14 файлах).
//
// Получатель `HTTP` в нижнем регистре проходит фильтр получателей (`http`),
// поэтому глагол в имени обёртки нельзя просто добавить к методам HttpClient:
// первым аргументом окажется `this.http`, а не адрес. Адрес — второй
// позиционный аргумент, у `requestVersioned` — третий, а метод — второй.
//
// Тела обёрток сами зовут HttpClient с адресом-параметром: статически такой
// вызов не восстановить, и он не должен считаться невосстановленным вызовом
// продукта, как только обёртка объявлена.
export module HTTP {
  export function getVersioned(http: HttpClient, url: string, version = ''): Observable<HttpResponse<unknown>> {
    return http.get(url, { observe: 'response', headers: createHeaders(version) });
  }

  export function postVersioned(http: HttpClient, url: string, body: unknown, version = ''): Observable<HttpResponse<unknown>> {
    return http.post(url, body, { observe: 'response', headers: createHeaders(version) });
  }

  export function putVersioned(http: HttpClient, url: string, body: unknown, version = ''): Observable<HttpResponse<unknown>> {
    return http.put(url, body, { observe: 'response', headers: createHeaders(version) });
  }

  export function deleteVersioned(http: HttpClient, url: string, version = ''): Observable<HttpResponse<unknown>> {
    return http.delete(url, { observe: 'response', headers: createHeaders(version) });
  }

  // Метод — из аргумента. `HttpClient.request` разбор не видит вовсе:
  // у него первый аргумент — метод, а не адрес.
  export function requestVersioned(
    http: HttpClient,
    method: string,
    url: string,
    version = '',
    body?: unknown,
  ): Observable<HttpResponse<unknown>> {
    return http.request(method, url, { observe: 'response', headers: createHeaders(version), body });
  }

  function createHeaders(version: string): HttpHeaders {
    return version ? new HttpHeaders().set('If-Match', version) : new HttpHeaders();
  }
}
