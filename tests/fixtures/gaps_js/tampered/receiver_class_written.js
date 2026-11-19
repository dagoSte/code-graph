let Service = require("./a.js");
Service = require("./b.js");
function drive() { return Service.polish(); }
module.exports = { drive };
