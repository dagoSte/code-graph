const { createRequire } = require("module");
function plain() { return require("./b.js").polish(); }
function created() {
  const require = createRequire(__filename);
  return require("./a.js").polish();
}
module.exports = { plain, created };
