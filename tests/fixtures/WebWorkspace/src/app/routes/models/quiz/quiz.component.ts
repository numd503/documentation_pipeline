import { Component, inject } from '@angular/core';

import { AuditService } from '@shared/services/audit.service';
import { ItemsService } from '@shared/services/items.service';

// Якорь этой страницы — маршрут `/models/loader/quiz`, а не имя класса:
// маршрут знает пользователь и на него ведёт закладка, `QuizComponent`
// не знает никто снаружи.
@Component({
  selector: 'app-loader-quiz',
  standalone: true,
  template: '<section>quiz</section>',
})
export class QuizComponent {
  // Внедрение функцией, а не конструктором. Тип здесь — аргумент `inject`,
  // и другого источника типа у поля нет; в боевом модуле таких девять
  // против сотен конструкторов, но без них ребро просто не возникнет.
  private readonly audit = inject(AuditService);

  constructor(private items: ItemsService) {}

  load(): void {
    this.items.dictionaries();
  }

  track(): void {
    this.audit.log({ page: 'quiz' });
  }
}
