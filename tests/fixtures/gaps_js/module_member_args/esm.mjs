import * as users from "./users.js";
import Decoy from "./decoy.js";
export function register(router) { router.get("/esm", users.show); router.get("/decoy", Decoy.show); }
