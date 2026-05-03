const path = require("path");
const fs = require("fs");
const fse = require("fs-extra");
const { spawn } = require("child_process");
const { getInstallationToken } = require("../githubApp");

function run(cmd, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: "inherit",
      ...options
    });

    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} ${args.join(" ")} exited with ${code}`));
    });
  });
}

function buildAuthedRepoUrl(repo, token) {
  return `https://x-access-token:${token}@github.com/${repo}.git`;
}

async function cloneOrPull({ siteId, repo, branch = "main", installationId }) {
  if (!siteId) throw new Error("siteId required");
  if (!repo) throw new Error("repo required");
  if (!installationId) throw new Error("github_installation_id required");

  const workspaceDir = path.join("/opt/hive/workspaces", siteId);
  const repoDir = path.join(workspaceDir, "repo");
  const gitDir = path.join(repoDir, ".git");

  await fse.mkdirp(workspaceDir);

  const token = await getInstallationToken(installationId);
  const repoUrl = buildAuthedRepoUrl(repo, token);

  if (!fs.existsSync(gitDir)) {
    await fse.remove(repoDir);
    await run("git", ["clone", "--branch", branch, repoUrl, repoDir]);
  } else {
    await run("git", ["-C", repoDir, "fetch", "origin", branch]);
    await run("git", ["-C", repoDir, "checkout", branch]);
    await run("git", ["-C", repoDir, "reset", "--hard", `origin/${branch}`]);
  }

  return { workspaceDir, repoDir };
}

module.exports = { cloneOrPull };
