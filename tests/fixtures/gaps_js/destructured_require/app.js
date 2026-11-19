const { polish, shine: gloss, Service } = require("./tools.js");
function drive() { return polish() + gloss() + Service.run(); }
function shadowed(polish) { return polish(); }
function local() { function polish() { return "L"; } return polish(); }
let { polish: later } = require("./tools.js"); later = () => "X";
function useLater() { return later(); }
module.exports = { drive, shadowed, local, useLater, polish };
