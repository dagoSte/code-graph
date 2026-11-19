const mod = require("./a.js");
mod.polish &&= () => "SHADOW";
mod.other ??= () => "SHADOW";
function drive() { return mod.polish() + mod.other(); }
module.exports = { drive };
