import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Прямой вызов с литералом: единственная форма, которую разбор восстанавливает
// без всякой настройки.
@Injectable({ providedIn: 'root' })
export class InfoService {
  constructor(private http: HttpClient) {}

  getInfo(): Observable<unknown> {
    return this.http.get('api/info');
  }
}
