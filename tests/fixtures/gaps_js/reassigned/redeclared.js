var mod = require("./a.js");
var mod = require("./b.js");
function drive() { return mod.polish(); }
function nested(flag) {
  var local = require("./a.js");
  if (flag) { var local = require("./b.js"); }
  return local.polish();
}
module.exports = { drive, nested };
