const debug = require("./debug.js");
const checkTree = (tree) => { if (!tree) throw new Error("no tree"); return tree; };
module.exports = (tree) => tree;
debug(() => module.exports = checkTree);
