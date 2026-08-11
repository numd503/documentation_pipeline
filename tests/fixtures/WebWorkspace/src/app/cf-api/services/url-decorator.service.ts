import { Injectable } from '@angular/core';

import { environment } from '@env/environment';

// Конвейер преобразования URL: рабочая, но выключенная конструкция. Выводить
// её результат статически означало бы исполнять `reduce` по списку декораторов,
// собираемому при старте приложения, поэтому преобразование задаётся
// конфигурацией (`web.url_rewrite`), а не выводится из этого кода.
@Injectable({ providedIn: 'root' })
export class UrlDecoratorService {
  private readonly excludeUrls: string[] = [];

  fixUrl(url: string): string {
    if (this.excludeUrls.some((excluded) => url.startsWith(excluded))) {
      return url;
    }
    return `${environment.apiRoot}${url}`;
  }
}
