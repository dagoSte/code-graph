const sort = require("./sort.js");
function byName() { return "local"; }
function ordered(people) { return people.sort(sort.byName); }
module.exports = { ordered, byName };
