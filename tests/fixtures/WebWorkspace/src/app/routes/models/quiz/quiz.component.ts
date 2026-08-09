import { Component } from '@angular/core';

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
  constructor(private items: ItemsService) {}

  load(): void {
    this.items.dictionaries();
  }
}
