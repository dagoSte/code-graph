function first() { return "A"; }
function second() { return "B"; }
try { throw {}; } catch (module) { module.exports = first; }
module.exports = second;
