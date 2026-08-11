import { Component } from '@angular/core';

import { WidgetService } from './widget.service';

@Component({
  selector: 'widget-root',
  standalone: true,
  template: '<div>widget</div>',
})
export class WidgetComponent {
  constructor(private widgets: WidgetService) {}

  refresh(): void {
    this.widgets.periods();
  }
}
