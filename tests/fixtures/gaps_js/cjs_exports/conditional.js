function plain() { return 1; }
function debug() { return 2; }
module.exports = plain;
if (process.env.DEBUG) module.exports = debug;
