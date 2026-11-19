const mod = require("./a.js"); const fake = () => "SHADOW";
function tag(s, m) { m.polish = fake; } tag`${mod}`;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
