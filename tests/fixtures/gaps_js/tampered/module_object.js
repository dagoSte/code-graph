const other = require("./b.js");
module.constructor.prototype.require = () => ({});
function drive() { return other.polish(); }
module.exports = { drive };
