function outer(require) {
  const { createRequire } = require("module");
  const nodeModule = require("module");
  function viaName() { const require = createRequire(__filename); return require("./a.js").polish(); }
  function viaMember() { const require = nodeModule.createRequire(__filename); return require("./a.js").other(); }
  return viaName() + viaMember();
}
module.exports = { outer };
