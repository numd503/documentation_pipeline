export class LoadOrders {
  static readonly type = '[Orders] Load';
}

export class SelectOrder {
  static readonly type = '[Orders] Select';

  constructor(public readonly id: string) {}
}
