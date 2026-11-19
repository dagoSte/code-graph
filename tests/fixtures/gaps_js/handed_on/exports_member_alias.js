const mod = require("./a.js"); const fake = () => "SHADOW";
exports.m = mod; exports.m.polish = fake;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
