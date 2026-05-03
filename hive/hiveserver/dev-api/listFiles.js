const fs = require("fs");
const path = require("path");

const ROOT = "/opt/hive/workspaces";

function walk(dir, base = "") {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  let results = [];

  for (const e of entries) {
    if (["node_modules", ".git"].includes(e.name)) continue;

    const full = path.join(dir, e.name);
    const rel = path.join(base, e.name);

    if (e.isDirectory()) {
      results.push({ path: rel, type: "dir" });
      results = results.concat(walk(full, rel));
    } else {
      results.push({ path: rel, type: "file" });
    }
  }

  return results;
}

module.exports = function listFiles(siteId) {
  const repoPath = path.join(ROOT, siteId, "repo");
  if (!fs.existsSync(repoPath)) {
    throw new Error("Repo not found");
  }
  return walk(repoPath);
};
