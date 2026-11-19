const parameter = require("./parameter.js");
const block = require("./block.js");
const variable = require("./variable.js");
const arrow = require("./arrow.js");
const caught = require("./caught.js");
const pattern = require("./pattern.js");
const exports_param = require("./exports_param.js");
const delayed = require("./delayed.js");
const overridden = require("./overridden.js");
function use_parameter() { return parameter(); }
function use_block() { return block(); }
function use_variable() { return variable(); }
function use_arrow() { return arrow(); }
function use_caught() { return caught(); }
function use_pattern() { return pattern(); }
function use_exports_param() { return exports_param.f(); }
function use_delayed() { return delayed.f(); }
function use_overridden() { return overridden.f(); }
module.exports = { use_parameter, use_block, use_variable, use_arrow, use_caught, use_pattern, use_exports_param, use_delayed, use_overridden };
