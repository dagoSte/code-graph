function first() { return "A"; }
function second() { return "B"; }
function fake() { const module = {}; module.exports = first; }
module.exports = second;
fake();
