// Хвостовой построитель query-строки — форма squidex (`${StringHelper.buildQuery(…)}`,
// 13 мест). Возвращает `?a=1&b=2` или пустую строку: к пути он не добавляет
// ни одного сегмента.
export function buildQuery(query: Record<string, string>): string {
  const pairs = Object.keys(query)
    .sort()
    .map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(query[key])}`);
  return pairs.length ? `?${pairs.join('&')}` : '';
}
