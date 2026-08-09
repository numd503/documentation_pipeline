import { ListComponent } from './list.component';

// Обходом НЕ исключается: тестовые файлы бывают частью графа наследования.
// Отсеивается правилом классификации с причиной «тест».
describe('ListComponent', () => {
  it('объявлен', () => {
    expect(ListComponent).toBeTruthy();
  });
});
