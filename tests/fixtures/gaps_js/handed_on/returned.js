const mod = require("./a.js"); const fake = () => "SHADOW";
function get() { return mod; } get().polish = fake;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
