import { HttpClient } from '@angular/common/http';

// Два класса в одном файле и цепочка наследования длиной два: замыкание
// `InnerDebtService -> BaseApiService -> CoreService` обязано считаться
// транзитивно и через границу файла.
export abstract class CoreService {
  protected readonly retries: number = 3;
}

export abstract class BaseApiService extends CoreService {
  constructor(protected readonly http: HttpClient) {
    super();
  }
}
