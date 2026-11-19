function direct(require) { return require("./a.js").polish(); }
function bound(require) { const t = require("./a.js"); return t.polish(); }
function called(require) { return require("./a.js")(); }
function inner() {
  function require() { return { polish() { return "SHADOW"; } }; }
  return require("./a.js").polish();
}
function forwarded(require) { module.exports.again = require("./c.js"); }
module.exports = { direct, bound, called, inner, forwarded };
