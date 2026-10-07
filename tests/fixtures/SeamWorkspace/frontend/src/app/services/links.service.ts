import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ResourceLink } from '@app/framework/resource';

// Гипермедиа: адрес (и метод) приходят в ответе сервера — статически
// невыразимо, решение человека «не восстанавливается, причина».
@Injectable({ providedIn: 'root' })
export class LinksService {
  constructor(private http: HttpClient) {}

  // Метод и адрес — из ссылки. `HttpClient.request` разбор не видит:
  // первый аргумент там метод, а не адрес (squidex: 15 мест в 7 файлах).
  follow(link: ResourceLink): Observable<unknown> {
    return this.http.request(link.method, link.href);
  }

  // Та же гипермедиа через глагол: метод известен, адрес — из ответа.
  // Этот вызов виден и попадает в невосстановленные.
  fetch(link: ResourceLink): Observable<unknown> {
    return this.http.get(link.href);
  }
}
