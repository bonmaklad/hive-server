const fs = require("fs");
const path = require("path");

const ROOT = "/opt/hive/workspaces";

module.exports = function readFile(siteId, filePath) {
  if (filePath.includes("..")) throw new Error("Invalid path");

  const fullPath = path.join(ROOT, siteId, "repo", filePath);

  if (!fs.existsSync(fullPath)) {
    throw new Error("File not found");
  }

  if (fs.statSync(fullPath).size > 500_000) {
    throw new Error("File too large");
  }

  return fs.readFileSync(fullPath, "utf8");
};
