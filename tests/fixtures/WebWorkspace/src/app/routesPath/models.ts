import { Routes } from '@angular/router';

import { DetailComponent } from '../routes/models/detail/detail.component';
import { ListComponent } from '../routes/models/list/list.component';

// Сегмент, собранный выражением: путь до него ведёт, но собрать его нельзя.
// Это состояние `route_unresolved`, а не отсутствие узла: пропав из вывода,
// такая ветка занизила бы знаменатель покрытия страниц.
const archiveSegment = ['archive', new Date().getFullYear()].join('-');

export const modelsPath: Routes = [
  { path: '', component: ListComponent },
  {
    path: 'loader/quiz',
    loadComponent: () => import('../routes/models/quiz/quiz.component').then((m) => m.QuizComponent),
  },
  { path: ':id', component: DetailComponent },
  { path: archiveSegment, component: ListComponent },
  { path: 'old', redirectTo: '', pathMatch: 'full' },
];
