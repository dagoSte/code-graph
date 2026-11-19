function first() { return "A"; }
function second() { return "B"; }
module.exports = { f: first };
module.exports.f = second;
