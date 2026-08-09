import { CommonModule } from '@angular/common';
import { Component, NgModule } from '@angular/core';

// Класс НЕ экспортируется, и это меняет форму дерева: его декоратор лежит
// ВНУТРИ `class_declaration`, а не рядом с ним в `export_statement`.
// Реализация, перенесённая с .NET буквально (модификаторы и атрибуты —
// потомки объявления), соберёт декораторы только с одного уровня и не
// классифицирует ни одного компонента при нулевом числе ошибок разбора.
@Component({
  selector: 'app-legacy-banner',
  template: '<span>legacy</span>',
})
class LegacyBannerComponent {}

@NgModule({
  declarations: [LegacyBannerComponent],
  imports: [CommonModule],
  exports: [LegacyBannerComponent],
})
export class LegacyModule {}
