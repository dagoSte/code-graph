function first() { return "A"; }
function second() { return "B"; }
exports['\uD800'] = first;
exports['\uDFFF'] = first;
exports.f = second;
