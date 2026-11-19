const users = require("./users");
const sort = require("./sort.js");
users.show = () => "patched";
Reflect.set(sort, "byAge", () => 0);
function routes(router) { router.get("/a", users.show); return [1, 2].sort(sort.byAge); }
module.exports = { routes };
