import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiUrlConfig } from '@app/framework/api-url';
import { buildQuery } from '@app/framework/query';

import { environment } from '../../environments/environment';

// DTO в одном файле с сервисом — форма squidex (`apps.service.ts`, `help.service.ts`).
// Вызов приписывается каждому классифицированному узлу файла, и вызовы
// сервиса считаются дважды: у сервиса и у DTO.
export interface AppDto {
  name: string;
  label: string;
}

@Injectable({ providedIn: 'root' })
export class AppsService {
  // База без литерала: значение сборки, разбору неизвестное.
  private readonly base = environment.apiUrl;

  constructor(private http: HttpClient, private apiUrl: ApiUrlConfig) {}

  // Построитель: адрес — `const`, инициализированный вызовом построителя.
  list(): Observable<AppDto[]> {
    const url = this.apiUrl.buildUrl('/api/apps');
    return this.http.get<AppDto[]>(url);
  }

  // Второй метод со своим `const url` того же имени и своим построителем.
  get(app: string): Observable<AppDto> {
    const url = this.apiUrl.buildUrl(`/api/apps/${app}`);
    return this.http.get<AppDto>(url);
  }

  // Литеральный `const url` третьего метода. `const` собираются по всему файлу,
  // и первый литерал под этим именем подставляется во ВСЕ `this.http.get(url)`,
  // в `list` и `get` тоже: ключ правдоподобен и неверен (squidex, `help.service.ts:42`).
  archived(): Observable<AppDto[]> {
    const url = 'api/apps/archived';
    return this.http.get<AppDto[]>(url);
  }

  // Хвостовой построитель query: подстановка прилипла к последнему сегменту пути.
  search(query: Record<string, string>): Observable<AppDto[]> {
    return this.http.get<AppDto[]>(`api/apps/search${buildQuery(query)}`);
  }

  // Конкатенация с невосстановленной базой в начале.
  legacy(): Observable<AppDto[]> {
    return this.http.get<AppDto[]>(this.base + '/api/apps');
  }
}
