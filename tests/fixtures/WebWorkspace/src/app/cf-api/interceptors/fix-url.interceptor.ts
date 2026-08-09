import {
  HttpEvent,
  HttpHandler,
  HttpInterceptor,
  HttpRequest,
} from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '@env/environment';

import { UrlDecoratorService } from '../services/url-decorator.service';

// Экспортируемый класс с декоратором: `decorator` лежит в `export_statement`
// РЯДОМ с `class_declaration`. Ср. с `legacy.module.ts`, где у неэкспортируемого
// класса тот же декоратор оказывается ВНУТРИ `class_declaration`.
@Injectable()
export class FixUrlInterceptor implements HttpInterceptor {
  constructor(private urlDecorator: UrlDecoratorService) {}

  intercept(req: HttpRequest<unknown>, next: HttpHandler): Observable<HttpEvent<unknown>> {
    // Поля `apiUrl` нет ни в одном окружении — условие ложно всегда.
    if (environment?.apiUrl) {
      const url = this.urlDecorator.fixUrl(req.url);
      return next.handle(req.clone({ url }));
    }
    return next.handle(req);
  }
}
