const mod = require("./a.js");
mod["polish"] = () => "SHADOW";
function drive() { return mod.polish() + mod.other(); }
module.exports = { drive };
