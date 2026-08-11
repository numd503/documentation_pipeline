import { HttpInterceptorFn } from '@angular/common/http';

import { environment } from '@env/environment';

// Функциональный интерцептор: символ объявлен константой с типом-функцией,
// декоратора у него нет вовсе. Реализация, ищущая только классы с декораторами,
// пропустит его молча — а в боевом модуле так объявлены интерцепторы и guard'ы.
export const authInterceptor: HttpInterceptorFn = (req, next) =>
  next(req.clone({ setHeaders: { 'X-Api-Root': environment.apiRoot } }));
