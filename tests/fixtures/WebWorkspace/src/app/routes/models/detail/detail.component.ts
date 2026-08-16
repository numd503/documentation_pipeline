import { Component } from '@angular/core';
import { ActivatedRoute } from '@angular/router';

import { ModelService } from '@cf-api/resources/model.service';

@Component({
  selector: 'app-models-detail',
  standalone: true,
  template: '<article>{{ id }}</article>',
})
export class DetailComponent {
  readonly id = this.route.snapshot.paramMap.get('id');

  constructor(
    private route: ActivatedRoute,
    private models: ModelService,
  ) {}

  reload(): void {
    if (this.id) {
      this.models.byId(this.id);
    }
  }

  refresh(): void {
    // Цепочка и опциональный доступ разом. Ребро здесь одно — `forUpdate`:
    // у внешнего вызова получатель это вызов, а не поле, и `subscribe`
    // ребром быть не может. Реализация, берущая любое свойство подряд,
    // насчитала бы два обращения к сервису вместо одного.
    this.models?.forUpdate(this.id ?? '').subscribe();
  }
}
