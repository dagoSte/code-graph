const mod = require("./a.js"); const fake = () => "SHADOW";
mo\u0064.pol\u{69}sh = fake;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
