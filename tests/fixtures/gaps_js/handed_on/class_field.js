const mod = require("./a.js"); const fake = () => "SHADOW";
class Holder { m = mod; } new Holder().m.polish = fake;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
