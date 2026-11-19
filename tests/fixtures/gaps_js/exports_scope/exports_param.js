function first() { return "A"; }
function second() { return "B"; }
function fake(exports) { exports.f = first; }
exports.f = second;
fake({});
