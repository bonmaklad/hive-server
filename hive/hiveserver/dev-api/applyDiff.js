const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { applyPatch } = require("diff");

const WORKSPACES_DIR = "/opt/hive/workspaces";

function hashContent(content) {
  return crypto.createHash("sha256").update(content).digest("hex");
}

module.exports = function applyDiff(siteId, filePath, diffText) {
  if (!siteId || !filePath || !diffText) {
    throw new Error("siteId, path and diff are required");
  }

  if (filePath.includes("..")) {
    throw new Error("Invalid path");
  }

  const repoDir = path.join(WORKSPACES_DIR, siteId, "repo");
  const fullPath = path.join(repoDir, filePath);

  if (!fs.existsSync(fullPath)) {
    throw new Error("File does not exist");
  }

  const original = fs.readFileSync(fullPath, "utf8");
  const patched = applyPatch(original, diffText);

  if (patched === false) {
    throw new Error("Patch failed");
  }

  fs.writeFileSync(fullPath, patched, "utf8");

  return {
    ok: true,
    hash: hashContent(patched),
  };
};
