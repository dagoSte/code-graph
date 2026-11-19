const M = require("node:module");
M.unrelated = 1;
function drive() { const require = M.createRequire(__filename); return require("./a.js").polish(); }
module.exports = { drive };
