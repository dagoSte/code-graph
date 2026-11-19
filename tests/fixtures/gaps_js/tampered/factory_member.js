const M = require("node:module");
M.createRequire = () => () => ({ polish: () => "SHADOW" });
function drive() { const require = M.createRequire(__filename); return require("./a.js").polish(); }
module.exports = { drive };
