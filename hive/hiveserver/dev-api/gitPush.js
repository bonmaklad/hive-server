const { execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const WORKSPACES_DIR = "/opt/hive/workspaces";

module.exports = function gitPush(siteId, message) {
  if (!siteId) {
    throw new Error("siteId required");
  }

  const workspace = path.join(WORKSPACES_DIR, siteId);
  const repoDir = path.join(workspace, "repo");
  const metaPath = path.join(workspace, "dev.meta.json");

  if (!fs.existsSync(metaPath)) {
    throw new Error("Dev mode not running");
  }

  if (!fs.existsSync(repoDir)) {
    throw new Error("Repo not found");
  }

  const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
  const branch = meta.branch || "main";

  const run = (cmd) =>
    execSync(cmd, {
      cwd: repoDir,
      stdio: "pipe",
      env: process.env,
    }).toString();

  // ensure clean git state detection
  const status = run("git status --porcelain");

  if (!status.trim()) {
    return { ok: true, message: "No changes to commit" };
  }

  run("git add -A");

  const commitMsg =
    message ||
    `hive: update via Dev Mode (${new Date().toISOString()})`;

  run(`git commit -m "${commitMsg.replace(/"/g, '\\"')}"`);
  run(`git push origin ${branch}`);

  return {
    ok: true,
    pushed: true,
    branch,
    commit: commitMsg,
  };
};
