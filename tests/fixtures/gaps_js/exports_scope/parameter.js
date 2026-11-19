function first() { return "A"; }
function second() { return "B"; }
function fake(module) { module.exports = first; }
module.exports = second;
fake({});
