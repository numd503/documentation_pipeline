export enum ModelKind {
  Draft = 'draft',
  Published = 'published',
}

export interface Model {
  id: string;
  title: string;
  kind: ModelKind;
}
