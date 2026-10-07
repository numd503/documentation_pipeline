// База адреса бэка — значение сборки, а не кода: в дев-сборке запросы идут
// через прокси, в боевой — на этот адрес.
export const environment = {
  production: false,
  apiUrl: 'http://localhost:5000/',
};
