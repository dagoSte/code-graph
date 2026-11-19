export function drive() {
  return import('./tools.js').then(m => m.polish());
}
