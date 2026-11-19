function drive() {
  var require = require("module").createRequire(__filename);
  return require("./a.js").polish();
}
module.exports = { drive };
