import { Component, OnInit } from '@angular/core';

import { AppDto, AppsService } from '@app/services/apps.service';

// Страница: до неё доходит таблица роутов, вызовы — у сервиса, которого она зовёт.
@Component({
  selector: 'app-apps-page',
  standalone: true,
  template: '<ul><li *ngFor="let app of apps">{{ app.name }}</li></ul>',
})
export class AppsPageComponent implements OnInit {
  apps: AppDto[] = [];

  constructor(private appsService: AppsService) {}

  ngOnInit(): void {
    this.appsService.list().subscribe((apps) => (this.apps = apps));
  }
}
