const git = require('./git');
async function work() { await git.find(); return git.clone(); }
module.exports = { work };
