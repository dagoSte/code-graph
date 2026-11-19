const lone = require("./lone.js");
const pair = require("./pair.js");
function useLone() { return lone.f(); }
function usePair() { return pair.f() + pair["\u{1F600}"](); }
module.exports = { useLone, usePair };
