function first() { return "A"; }
function second() { return "B"; }
function patch() { module.exports.f = second; }
module.exports = { f: first };
if (process.env.PATCH) patch();
