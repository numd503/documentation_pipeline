import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { Order } from './order';

// Сервис раздела: его зовут и стейт, и страница списка напрямую.
// Ни одной странице он не принадлежит — до него дотягиваются обе.
@Injectable({ providedIn: 'root' })
export class OrdersApiService {
  constructor(private http: HttpClient) {}

  list(): Observable<Order[]> {
    return this.http.get<Order[]>('api/orders');
  }

  byId(id: string): Observable<Order> {
    return this.http.get<Order>(`api/orders/${id}`);
  }

  exportCsv(): Observable<Blob> {
    return this.http.post('api/orders/export', {}, { responseType: 'blob' });
  }
}
