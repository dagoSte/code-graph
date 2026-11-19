function polish() {
  return 1;
}
function drive(xs, el) {
  xs.map(polish);
  setTimeout(polish, 1);
  el.addEventListener('click', polish);
}
module.exports = { drive };
