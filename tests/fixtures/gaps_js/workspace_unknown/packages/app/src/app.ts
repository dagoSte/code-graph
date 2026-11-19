import { polish } from '@fx/utilities';
import { polish as other } from '@fx/utils';
export function drive(): number {
  return polish();
}
export function viaPackage(): number {
  return other();
}
