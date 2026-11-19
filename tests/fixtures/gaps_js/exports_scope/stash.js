const mod = require("./a.js");
function stash(module) { module.exports = mod; module.exports.polish = () => "SHADOW"; }
stash({});
function drive() { return mod.polish(); }
exports.drive = drive;
