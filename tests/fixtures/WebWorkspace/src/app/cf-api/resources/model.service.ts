import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { Model } from './model';

@Injectable({ providedIn: 'root' })
export class ModelService {
  // Литеральное значение поля. Без его разрешения три вызова ниже попадут
  // в «невосстановленные», хотя маршрут в них известен целиком.
  private readonly baseUrl: string = '/api/ml/debtsconsgroup';

  constructor(private http: HttpClient) {}

  list(): Observable<Model[]> {
    return this.http.get<Model[]>('api/ml/structure');
  }

  byId(id: string): Observable<Model> {
    return this.http.get<Model>(`api/ml/structure/${id}`);
  }

  forUpdate(id: string): Observable<Model> {
    return this.http.get<Model>('api/ml/structure/getForUpdate/' + id);
  }

  saveAlternative(body: Model): Observable<void> {
    // Подстановка в НАЧАЛЕ шаблона — это база, а не сегмент пути.
    return this.http.post<void>(`${this.baseUrl}/saveAlternative`, body);
  }

  removeAlternative(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/alternative/${id}`);
  }

  // URL приходит параметром: восстановить его нечем, и это отдельная категория,
  // а не мусорный ключ.
  download(url: string): Observable<Blob> {
    return this.http.get(url, { responseType: 'blob' });
  }
}
