import { polish } from "./tools.mjs";
export function drive() { return polish(); }
export function shadowed(polish) { return polish(); }
export function local() { function polish() { return "L"; } return polish(); }
export function block() { { const polish = () => "B"; return polish(); } }
export const arrow = (polish) => polish();
