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
}
