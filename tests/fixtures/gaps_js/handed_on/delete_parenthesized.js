const mod = require("./a.js"); const fake = () => "SHADOW";
delete (mod).polish;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
