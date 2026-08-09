import { ListComponent } from './list.component';

// Обходом НЕ исключается: тестовые файлы бывают частью графа наследования,
// и вот ровно такой класс — его причина. Отсеивается правилом классификации
// с причиной «тест», то есть решением, а не отсутствием решения.
export class ComponentHarness {
  constructor(readonly component: ListComponent) {}
}

describe('ListComponent', () => {
  it('объявлен', () => {
    expect(ListComponent).toBeTruthy();
  });
});
