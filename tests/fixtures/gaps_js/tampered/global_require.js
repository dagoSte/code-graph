const fake = () => ({ polish: () => "SHADOW" });
require = fake;
function drive() { return require("./a.js").polish(); }
module.exports = { drive };
