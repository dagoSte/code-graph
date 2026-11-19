const mod = require("./a.js"); const fake = () => ({ polish: () => "SHADOW" });
module = { require: fake }; exports.again = () => require("./a.js").polish();
function drive() { return mod.polish(); }
exports.drive = drive;
