// Единственное стабильное литеральное звено цепочки NGXS — строка типа экшена.
// Имя класса меняется при рефакторинге, строка — нет, поэтому в ключ идёт она.
export class LoadInnerDebts {
  static readonly type = '[Inner Debt] Load';

  constructor(public readonly clientId: string) {}
}

export class SaveInnerDebt {
  static readonly type = '[Inner Debt] Save';

  constructor(public readonly payload: unknown) {}
}
