import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

// Оболочка не стоит в таблице роутов: страницей она не становится,
// и до общих узлов через неё не дотягивается никто.
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  template: '<router-outlet></router-outlet>',
})
export class AppComponent {}
