function first() { return "A"; }
function second() { return "B"; }
const fake = (module) => { module.exports = first; };
module.exports = second;
fake({});
