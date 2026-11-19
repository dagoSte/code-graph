const mod = require("./a.js"); const fake = () => "SHADOW";
Reflect.set(mod, "polish", fake);
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
