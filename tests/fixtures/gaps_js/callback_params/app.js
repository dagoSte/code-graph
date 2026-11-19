function polish(x) { return x + 1; }
function run(polish) { return [1].map(polish); }
function use() { return [1].map(polish); }
function inner() { const polish = (x) => x + 2; return [1].map(polish); }
function caught() { try { throw (x) => x + 3; } catch (polish) { return [1].map(polish); } }
const viaArrow = (polish) => [1].map(polish);
function looped(list) { for (const polish of list) [1].map(polish); return list; }
function later() { return new Promise(polish); }
module.exports = { run, use, inner, caught, viaArrow, looped, later };
