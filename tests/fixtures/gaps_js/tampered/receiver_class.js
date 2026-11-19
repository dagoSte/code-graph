const Service = require("./a.js");
const fake = () => ({ polish: () => "SHADOW" });
require = fake;
function drive() { return Service.polish(); }
module.exports = { drive };
