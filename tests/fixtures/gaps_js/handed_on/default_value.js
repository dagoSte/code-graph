const mod = require("./a.js"); const fake = () => "SHADOW";
function set(x = mod) { x.polish = fake; } set();
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
