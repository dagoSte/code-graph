const runScript = async () => 1;
const isServer = () => false;
module.exports = Object.assign(runScript, { isServer });
