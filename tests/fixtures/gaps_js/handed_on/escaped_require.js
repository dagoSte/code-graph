const fake = () => ({ polish: () => "SHADOW" });
requir\u0065 = fake;
function drive() { return require("./a.js").polish(); }
module.exports = { drive };
