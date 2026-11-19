const replaced = require("./replaced.js");
const stale = require("./stale.js");
const conditional = require("./conditional.js");
const realias = require("./realias.js");
const instance = require("./instance.js");
const assign = require("./assign.js");
const props = require("./props.js");
const escaped = require("./escaped_target.js");
const member = require("./member_replaced.js");
const names = require("./member_names.js");
const alternatives = require("./member_alternatives.js");
const treeCheck = require("./tree_check.js");
const Service = require("./class_export.js");
function useReplaced() { return replaced(); }
function useStale() { return stale.kept() + stale.lost(); }
function useConditional() { return conditional(); }
function useRealias() { return realias() + realias.extra(); }
function useInstance() { return instance.run(); }
function useAssign() { return assign(); }
function useProps() { return props() + props.virtual(); }
function useEscaped() { return escaped.polish(); }
function useMember() { return member.polish() + names.pick(); }
function useAlternatives() { return alternatives.helper() + alternatives.timed(); }
function useTreeCheck(tree) { return treeCheck(tree); }
function useClass() { const service = Service.create(); return service.start() + service.describe(); }
module.exports = { useReplaced, useStale, useConditional, useRealias, useInstance, useAssign, useProps, useEscaped, useMember, useAlternatives, useTreeCheck, useClass };
