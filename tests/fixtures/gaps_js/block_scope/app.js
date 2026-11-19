const mod = require("./a.js");
function drive(flag) {
  if (flag) { const mod = require("./b.js"); mod.polish(); }
  return mod.polish();
}
module.exports = { drive };
