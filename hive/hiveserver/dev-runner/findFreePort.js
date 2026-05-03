const net = require("net");

function checkPort(port) {
  return new Promise((resolve) => {
    const server = net.createServer()
      .once("error", () => resolve(false))
      .once("listening", () => {
        server.close(() => resolve(true));
      })
      .listen(port, "0.0.0.0");
  });
}

async function findFreePort(start = 40100, end = 49999) {
  for (let port = start; port <= end; port++) {
    // eslint-disable-next-line no-await-in-loop
    const free = await checkPort(port);
    if (free) return port;
  }
  throw new Error("No free ports available");
}

module.exports = { findFreePort };
