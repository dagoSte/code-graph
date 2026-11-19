function first() { return "A"; }
function second() { return "B"; }
module.exports = second;
{ const module = {}; module.exports = first; }
