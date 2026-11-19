function plain() { return 1; }
function traced() { return 2; }
function early() { return 3; }
function later() { return 4; }
exports.helper = plain;
if (process.env.TRACE) exports.helper = traced;
setImmediate(() => { exports.timed = later; });
exports.timed = early;
