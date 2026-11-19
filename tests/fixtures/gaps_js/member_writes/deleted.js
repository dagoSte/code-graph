const mod = require("./a.js");
delete mod.other;
function drive() { return mod.polish(); }
module.exports = { drive };
