import { HttpClient } from '@angular/common/http';
import { Component } from '@angular/core';

@Component({
  selector: 'app-editor',
  standalone: true,
  template: '<pre>{{ text }}</pre>',
})
export class EditorComponent {
  // Поле с литералом выглядит константой, но его присваивают в `open`:
  // до S16 подстановка инициализатора давала `GET ''` вместо невосстановленного.
  public fileSource = '';
  public text = '';

  constructor(private http: HttpClient) {}

  open(src: string): void {
    this.fileSource = src;
    this.http.get(this.fileSource, { responseType: 'text' }).subscribe((text) => (this.text = text));
  }
}
