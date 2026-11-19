let mod = require("./a.js");
let other = require("./a.js");
[mod] = [{ polish() { return "SHADOW"; } }];
for (other of [{}]) {}
function drive() { return mod.polish() + other.other(); }
module.exports = { drive };
