const M = require("node:module"); const fake = () => () => ({ polish: () => "SHADOW" });
(M as any).createRequire = fake;
function drive() { if (typeof M.createRequire !== "function") return "NOT_CALLABLE"; const require = M.createRequire(__filename); return require("./a.js").polish(); }
module.exports = { drive };
