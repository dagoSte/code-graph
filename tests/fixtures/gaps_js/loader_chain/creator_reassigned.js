let { createRequire } = require("module");
function drive() {
  createRequire = () => () => ({ polish() { return "SHADOW"; } });
  const require = createRequire(__filename);
  return require("./a.js").polish();
}
module.exports = { drive };
