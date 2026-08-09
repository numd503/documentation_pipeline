import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Универсальный эндпоинт списков платформы: один маршрут на много смыслов.
// Ключ «метод + маршрут» склеил бы обращения к пользователям, к моделям
// и к справочникам в одну точку — то есть дал бы одну связь вместо трёх
// и потерял ровно тот смысл, ради которого связь строится.
@Injectable({ providedIn: 'root' })
export class ItemsService {
  constructor(private http: HttpClient) {}

  // Различитель в ТЕЛЕ запроса.
  users(): Observable<unknown[]> {
    return this.http.post<unknown[]>('api/items/query', {
      listInnerName: 'users',
      fields: ['Id', 'Login'],
    });
  }

  models(): Observable<unknown[]> {
    return this.http.post<unknown[]>('api/items/query', {
      listInnerName: 'models',
      fields: ['Id', 'Title'],
    });
  }

  // Различитель в QUERY-строке того же самого API — второе место, и одного
  // правила на оба случая не хватает.
  dictionaries(): Observable<unknown[]> {
    return this.http.get<unknown[]>('api/items?listInnerName=dictionaries');
  }

  // Различитель собран подстановкой: маршрут известен, смысл — нет.
  // Это `registry_unresolved` — состояние, а не ошибка.
  byType(type: string): Observable<unknown[]> {
    return this.http.get<unknown[]>(`api/items?listInnerName=${type}`);
  }
}
