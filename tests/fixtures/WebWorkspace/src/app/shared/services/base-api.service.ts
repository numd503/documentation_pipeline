import { HttpClient } from '@angular/common/http';

import { AuditService } from './audit.service';

// Два класса в одном файле и цепочка наследования длиной два: замыкание
// `InnerDebtService -> BaseApiService -> CoreService` обязано считаться
// транзитивно и через границу файла.
export abstract class CoreService {
  protected readonly retries: number = 3;

  // Зависимость объявлена в БАЗЕ, а зовут её в наследнике. Без обхода
  // замыкания наследования такое обращение остаётся неразрешённым — и это
  // не редкость, а форма: на боевом модуле сервисы поголовно наследуют
  // общий базовый класс с внедрёнными зависимостями.
  constructor(protected readonly audit: AuditService) {}
}

export abstract class BaseApiService extends CoreService {
  constructor(
    protected readonly http: HttpClient,
    audit: AuditService,
  ) {
    super(audit);
  }
}
