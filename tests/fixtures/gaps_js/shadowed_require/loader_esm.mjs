import { createRequire as make } from "node:module";
import * as nodeModule from "module";
export function viaImport() { const require = make(import.meta.url); return require("./a.js").polish(); }
export function viaNamespace() { const require = nodeModule.createRequire(import.meta.url); return require("./b.js").polish(); }
