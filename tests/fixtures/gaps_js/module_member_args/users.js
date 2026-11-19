function list(req, res) { return "users.list"; }
function load(req, res, next, id) { return "users.load"; }
function show(req, res) { return "users.show"; }
module.exports = { list, load, show };
