const M = require("node:module"); const fake = () => () => ({ polish: () => "SHADOW" });
M["create\x52equire"] = fake;
function drive() { if (typeof M.createRequire !== "function") return "NOT_CALLABLE"; const require = M.createRequire(__filename); return require("./a.js").polish(); }
module.exports = { drive };
