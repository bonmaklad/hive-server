const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const WORKSPACES_DIR = "/opt/hive/workspaces";

function hashContent(content) {
  return crypto.createHash("sha256").update(content).digest("hex");
}

module.exports = function writeFile(siteId, filePath, content, expectedHash) {
  if (!siteId || !filePath) {
    throw new Error("siteId and path are required");
  }

  if (filePath.includes("..")) {
    throw new Error("Invalid path");
  }

  const repoDir = path.join(WORKSPACES_DIR, siteId, "repo");
  const fullPath = path.join(repoDir, filePath);

  if (!fs.existsSync(fullPath)) {
    throw new Error("File does not exist");
  }

  const current = fs.readFileSync(fullPath, "utf8");
  const currentHash = hashContent(current);

  if (expectedHash && expectedHash !== currentHash) {
    throw new Error("File has changed on disk");
  }

  fs.writeFileSync(fullPath, content, "utf8");

  return {
    ok: true,
    hash: hashContent(content),
  };
};
