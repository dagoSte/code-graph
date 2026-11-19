const other = require("./b.js");
let createRequire = null;
createRequire = () => null;
function drive() { return other.polish(); }
module.exports = { drive };
