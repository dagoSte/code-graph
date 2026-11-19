const path = require('path');
function polish() {
  return 1;
}
function getHandler() {
  return polish;
}
function drive(name) {
  return getHandler()();
}
function load(name) {
  return require(path.join(__dirname, name));
}
function later(name) {
  return import(name);
}
module.exports = { drive, load, later };
