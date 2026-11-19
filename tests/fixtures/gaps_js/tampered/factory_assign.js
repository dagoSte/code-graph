const M = require("module");
const other = require("./b.js");
Object.assign(M, { createRequire: () => () => ({}) });
function drive() { return other.polish(); }
module.exports = { drive };
