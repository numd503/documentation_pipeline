import { Injectable } from '@angular/core';

import { environment } from '../../environments/environment';

// Построитель адреса — форма squidex (`this.apiUrl.buildUrl(…)`, 78 вызовов
// с причиной «значение переменной»). Путь — первый аргумент, база — из сборки.
// Сам построитель HTTP не зовёт: связь видна только у вызывающего.
@Injectable({ providedIn: 'root' })
export class ApiUrlConfig {
  public readonly value: string = environment.apiUrl;

  public buildUrl(path: string): string {
    const trimmed = path.startsWith('/') ? path.substring(1) : path;
    return `${this.value}${trimmed}`;
  }
}
