import { Routes } from '@angular/router';

import { ShellComponent } from './routes/shell.component';
import { modelsPath } from './routesPath/models';

// Дерево страниц собрано СПРЕДОМ импортированного массива, а не ленивым import().
// В боевом модуле `loadChildren` — ноль, `RouterModule.forRoot/forChild` — ноль,
// а 26 записей `path:` разложены по файлам `routesPath/*.ts`. Разбор одного этого
// файла даёт два пути из двадцати шести, и молча: ошибок разбора нет, узлы
// просто не появляются.
export const appRoutes: Routes = [
  {
    path: '',
    component: ShellComponent,
    children: [
      { path: 'models', children: [...modelsPath] },
      // Вторая межфайловая форма — ленивая загрузка массива роутов.
      {
        path: 'forecast',
        loadChildren: () => import('./routesPath/lazy').then((m) => m.lazyRoutes),
      },
      { path: '**', redirectTo: 'models', pathMatch: 'full' },
    ],
  },
];
