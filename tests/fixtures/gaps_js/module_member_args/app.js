const express = require("express");
const users = require("./users");
const sort = require("./sort.js");
const router = express.Router();
router.get("/users", users.list);
router.param("id", users.load);
function ordered(people) { return people.sort(sort.byName); }
function mount(users) { router.get("/shown", users.show); }
const local = { show() { return "local.show"; } };
router.get("/local", local.show);
module.exports = { router, ordered, mount };
