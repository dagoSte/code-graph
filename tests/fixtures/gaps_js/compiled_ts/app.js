"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const mod_1 = require("./mod");
function drive() { return (0, mod_1.fn)() + (0, mod_1.helper)(); }
async function later() { return await (0, mod_1.fn)(); }
function direct() { return (0, local)(); }
function local() { return 3; }
function await(x) { return x; }
function sloppy() { return await(0, local)(); }
module.exports = { drive, later, direct, sloppy };
