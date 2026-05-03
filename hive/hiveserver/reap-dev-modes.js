const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const WORKSPACES_DIR = "/opt/hive/workspaces";
const IDLE_LIMIT_MS = 5 * 60 * 1000; // 5 minutes

const now = Date.now();

if (!fs.existsSync(WORKSPACES_DIR)) process.exit(0);

const siteDirs = fs.readdirSync(WORKSPACES_DIR, { withFileTypes: true })
  .filter(d => d.isDirectory())
  .map(d => d.name);

for (const siteId of siteDirs) {
  const lastAccessFile = path.join(WORKSPACES_DIR, siteId, "last_access");
  const pidFile = path.join(WORKSPACES_DIR, siteId, "dev.pid");

  if (!fs.existsSync(pidFile)) continue; // not running
  if (!fs.existsSync(lastAccessFile)) continue; // no activity ever recorded

  const lastAccess = Number(fs.readFileSync(lastAccessFile, "utf8"));
  if (Number.isNaN(lastAccess)) continue;

  const idleTime = now - lastAccess;

  if (idleTime > IDLE_LIMIT_MS) {
    console.log(`Stopping Dev Mode for ${siteId} (idle ${Math.round(idleTime / 1000)}s)`);

    spawnSync(
      "node",
      ["/opt/hive/hiveserver/dev-runner/stopDev.js", siteId],
      { stdio: "inherit" }
    );
  }
}
