import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

export interface RestRequest {
  method: string;
  url: string;
  body?: unknown;
}

// Обёртка с объектом-запросом — форма abp (`restService.request({ method, url })`,
// 119 вызовов, почти все — в сгенерированных `proxy/`). Адрес и метод — поля
// первого аргумента, а не позиционные аргументы.
@Injectable({ providedIn: 'root' })
export class RestService {
  constructor(private http: HttpClient) {}

  request<T>(config: RestRequest): Observable<T> {
    return this.http.request<T>(config.method, config.url, { body: config.body });
  }
}
