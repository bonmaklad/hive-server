const fs = require("fs");
const path = require("path");

function pidFile(siteId) {
  return path.join("/opt/hive/workspaces", siteId, "dev.pid");
}

function metaFile(siteId) {
  return path.join("/opt/hive/workspaces", siteId, "dev.meta.json");
}

function stopDev(siteId) {
  if (!siteId) {
    throw new Error("siteId is required");
  }

  const pf = pidFile(siteId);

  if (!fs.existsSync(pf)) {
    return {
      ok: true,
      siteId,
      stopped: false,
      reason: "not running"
    };
  }

  const pid = Number(fs.readFileSync(pf, "utf8"));

  if (!pid || Number.isNaN(pid)) {
    fs.unlinkSync(pf);
    return {
      ok: true,
      siteId,
      stopped: false,
      reason: "invalid pid"
    };
  }

  try {
    process.kill(pid, "SIGTERM");
  } catch (_) {}

  fs.unlinkSync(pf);

  const mf = metaFile(siteId);
  if (fs.existsSync(mf)) {
    const meta = JSON.parse(fs.readFileSync(mf, "utf8"));
    meta.stoppedAt = new Date().toISOString();
    fs.writeFileSync(mf, JSON.stringify(meta, null, 2));
  }

  return {
    ok: true,
    siteId,
    stopped: true,
    pid
  };
}

/* CLI support */
if (require.main === module) {
  const siteId = process.argv[2];

  if (!siteId) {
    console.error("Usage: node stopDev.js <siteId>");
    process.exit(1);
  }

  try {
    const result = stopDev(siteId);
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error("❌ stopDev failed");
    console.error(err);
    process.exit(1);
  }
}

module.exports = stopDev;
