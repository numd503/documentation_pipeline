import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Внешний адрес: хост — не наш бэк. Нормализация маршрута срезает хост,
// и без него вызов неотличим от обращения к своему `feed.json`
// (squidex: `help.service.ts`, `stock-photo.service.ts`).
@Injectable({ providedIn: 'root' })
export class FeedService {
  constructor(private http: HttpClient) {}

  latest(): Observable<unknown> {
    return this.http.get('https://ext.example.org/feed.json');
  }
}
