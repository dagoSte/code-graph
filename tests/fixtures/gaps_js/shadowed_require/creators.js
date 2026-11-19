function fakecreateRequire() { return () => ({ polish() { return "SHADOW"; } }); }
function viaParameter(createRequire) { const require = createRequire(); return require("./a.js").polish(); }
function viaFake() { const require = fakecreateRequire(); return require("./a.js").polish(); }
function viaLocal() {
  function createRequire() { return fakecreateRequire(); }
  const require = createRequire();
  return require("./a.js").polish();
}
module.exports = { viaParameter, viaFake, viaLocal };
