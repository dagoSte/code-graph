var mod = require("./a.js");
var mod = class { static polish() { return "SHADOW"; } };
function drive() { return mod.polish(); }
module.exports = { drive };
