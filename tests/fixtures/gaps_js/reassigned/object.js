let mod = require("./a.js");
mod = { polish() { return 0; } };
function drive() { return mod.polish(); }
module.exports = { drive };
