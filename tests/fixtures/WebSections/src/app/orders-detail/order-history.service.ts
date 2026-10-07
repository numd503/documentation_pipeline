import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

export interface OrderEvent {
  at: string;
  status: string;
}

// Приватный сервис страницы: до него дотягивается одна `orders-detail`,
// поэтому он описывается внутри её документа и кандидатом в раздел не бывает.
@Injectable({ providedIn: 'root' })
export class OrderHistoryService {
  constructor(private http: HttpClient) {}

  byOrder(id: string): Observable<OrderEvent[]> {
    return this.http.get<OrderEvent[]>(`api/orders/${id}/history`);
  }
}
