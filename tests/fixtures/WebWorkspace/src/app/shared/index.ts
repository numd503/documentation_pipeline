// Бочка: имя импортируется не оттуда, где объявлено. Резолв, знающий только
// про относительные пути и алиасы, найдёт здесь ноль объявлений и потеряет
// цепочку наследования, ничего при этом не сообщив.
export * from './services/audit.service';
export * from './services/base-api.service';
export * from './services/items.service';
