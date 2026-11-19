let mod = require("./a.js");
mod = require("./b.js");
function drive() { return mod.polish(); }
module.exports = { drive };
