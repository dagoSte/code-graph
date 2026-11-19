const Users = require("./users");
class Router { get(path, fn) { return fn; } }
function wire(router) { return router.get("/c", Users.list); }
module.exports = { wire, Router };
