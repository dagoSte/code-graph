const M = require("module");
const other = require("./b.js");
M["createRequire"] = () => () => ({});
function drive() { return other.polish(); }
module.exports = { drive };
