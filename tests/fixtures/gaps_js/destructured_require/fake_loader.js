const fake = () => ({ polish: () => "SHADOW" });
function load(require) { const { polish } = require("./tools.js"); return polish(); }
module.exports = { drive: () => load(fake) };
