const mod = require("./a.js");
function loop(list) {
  for (const mod of list) { mod.polish(); }
  try { list.run(); } catch (mod) { mod.polish(); }
  list.forEach(() => { let mod = list; mod.polish(); });
  return mod.polish();
}
module.exports = { loop };
