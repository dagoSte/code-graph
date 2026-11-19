const m = require("./a.js"); const fake = () => "SHADOW"; m.polish = fake;
const { polish } = require("./a.js");
function drive() { return polish(); }
module.exports = { drive };
