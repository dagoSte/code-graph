const mod = require("./a.js");
const alt = require("./a.js");
Object.assign(mod, { polish() { return "SHADOW"; } });
Object.defineProperty(alt, "polish", { value: () => "SHADOW" });
function drive() { return mod.polish() + alt.other(); }
module.exports = { drive };
