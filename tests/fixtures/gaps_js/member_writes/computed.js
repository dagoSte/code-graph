const mod = require("./a.js");
const key = process.argv[2];
mod[key] = () => "SHADOW";
function drive() { return mod.polish() + mod.other(); }
module.exports = { drive };
