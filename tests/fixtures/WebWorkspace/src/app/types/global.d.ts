// Файл деклараций: обходом НЕ исключается (он нужен резолву), отсеивается
// правилом классификации с причиной «декларация».
declare module '*.svg' {
  const content: string;
  export default content;
}
