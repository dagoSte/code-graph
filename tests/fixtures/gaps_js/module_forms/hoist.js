function early() { return tools.polish(); }
function shadow(items) { return items.map(tools => tools.polish()); }
const tools = require('./tools');
module.exports = { early, shadow };
