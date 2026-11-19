module.exports = process.env.CHECK_TREE ? (fn) => fn() : () => {};
