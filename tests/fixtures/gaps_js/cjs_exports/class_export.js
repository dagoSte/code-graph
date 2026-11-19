class Base { describe() { return "base"; } }
module.exports = class Service extends Base {
  static create() { return new this(); }
  start() { return 1; }
};
