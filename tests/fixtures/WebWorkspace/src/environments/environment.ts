// Поля `apiUrl` здесь нет намеренно, и это воспроизведение факта, а не упрощение:
// в боевом модуле его нет ни в одном окружении, поэтому ветка `if (environment?.apiUrl)`
// в FixUrlInterceptor не выполняется ни в одной сборке, и URL уходит в сеть таким,
// каким записан в сервисе.
export const environment = {
  production: false,
  apiRoot: '/api',
};
