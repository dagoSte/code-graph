const app = require("./app.js");
const control = require("./control.js");
function viaForward() { return app.again(); }
function viaControl() { return control.okay(); }
module.exports = { viaForward, viaControl };
