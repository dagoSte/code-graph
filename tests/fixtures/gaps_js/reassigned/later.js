let mod = require("./a.js");
function swap() { mod = require("./b.js"); }
function drive() { return mod.polish(); }
module.exports = { drive, swap };
