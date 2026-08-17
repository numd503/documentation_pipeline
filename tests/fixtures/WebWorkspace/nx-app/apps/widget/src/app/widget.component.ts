import { Component, OnInit } from '@angular/core';

import { WidgetService } from './widget.service';

@Component({
  selector: 'widget-root',
  standalone: true,
  template: '<div>widget</div>',
})
export class WidgetComponent implements OnInit {
  // Поле с аннотацией типа: присваивается в `ngOnInit`, а не внедряется
  // конструктором. Форма не менее однозначна, чем параметр, и на боевом
  // модуле такие поля — половина из 63 % неразрешённых обращений.
  private later!: WidgetService;

  constructor(private widgets: WidgetService) {}

  ngOnInit(): void {
    this.later = this.widgets;
    this.later.periods();
  }

  refresh(): void {
    this.widgets.periods();
  }
}
