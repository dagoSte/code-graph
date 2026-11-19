const mod = require("./a.js"); const fake = () => "SHADOW";
const { polish: p } = mod; if (typeof mod === "object" && mod.other) p.extra = 1;
function drive() { return typeof mod.polish === "function" ? mod.polish() : "NOT_CALLABLE"; }
module.exports = { drive };
