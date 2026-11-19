function handle() { return "wrong"; }
function other() { return "right"; }
async function run(handle) { console.log(await (0, handle)()); }
run(other);
