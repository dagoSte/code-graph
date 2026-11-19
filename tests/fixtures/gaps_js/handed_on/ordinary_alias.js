const mod = require("./a.js"); const fake = () => "SHADOW";
const other = mod; other.polish = fake;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
