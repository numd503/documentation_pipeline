import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

// Шаблон задан строкой, а не `templateUrl`: у такого компонента внешнего
// `.html` нет вовсе, и `impl_hash` обязан собираться без него, а не падать.
@Component({
  selector: 'app-shell',
  standalone: true,
  imports: [RouterOutlet],
  template: '<router-outlet></router-outlet>',
})
export class ShellComponent {}
