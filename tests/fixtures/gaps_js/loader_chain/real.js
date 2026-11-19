const nodeModule = require("node:module");
function viaMember() {
  const require = nodeModule.createRequire(__filename);
  return require("./a.js").polish();
}
module.exports = { viaMember };
