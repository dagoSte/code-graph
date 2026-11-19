let mod = require("./a.js");
mod ||= {};
mod ??= {};
function drive() { return mod.polish(); }
module.exports = { drive };
