const mod = require("./a.js");
mod["label"] = "kept";
function drive() { return mod.polish(); }
module.exports = { drive };
