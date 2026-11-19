const mod = require("./a.js"); const fake = () => "SHADOW";
mod["pol\x69sh"] = fake;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
