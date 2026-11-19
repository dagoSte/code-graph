const mod = require("./a.js"); const fake = () => "SHADOW";
for (const x of [mod]) x.polish = fake;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
