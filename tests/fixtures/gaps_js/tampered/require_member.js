const other = require("./b.js");
require.cache = Object.create(null);
function drive() { return other.polish(); }
module.exports = { drive };
