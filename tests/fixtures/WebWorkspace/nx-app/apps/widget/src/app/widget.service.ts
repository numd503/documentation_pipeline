import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Модуль живёт в другой форме workspace и в таблице `web.url_rewrite` не назван.
// Литерал здесь записан С префиксом приложения: сравнение ключей как есть
// разведёт его и маршрут контроллера `api/limits/getperiods` по двум корзинам
// и объявит сразу и «вызов без эндпоинта», и «эндпоинт без вызывающего».
@Injectable({ providedIn: 'root' })
export class WidgetService {
  constructor(private http: HttpClient) {}

  periods(): Observable<unknown[]> {
    return this.http.get<unknown[]>('/pm/api/limits/getperiods');
  }
}
