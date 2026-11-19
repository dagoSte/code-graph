const mod = require("./a.js"); const fake = () => "SHADOW";
module.exports.mod = mod;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
