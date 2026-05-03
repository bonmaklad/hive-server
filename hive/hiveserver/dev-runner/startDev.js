const fs = require("fs");
const path = require("path");
const fse = require("fs-extra");
const { spawn } = require("child_process");

const { cloneOrPull } = require("./cloneRepo");
const { detectFramework } = require("./detectFramework");
const { findFreePort } = require("./findFreePort");
const { getSiteById } = require("../supabase");
const patchNextConfig = require("./patchNextConfig");

const WORKSPACES_DIR = "/opt/hive/workspaces";
const MAX_DEV_SESSIONS = 5;

function pidFile(siteId) {
  return path.join(WORKSPACES_DIR, siteId, "dev.pid");
}

function metaFile(siteId) {
  return path.join(WORKSPACES_DIR, siteId, "dev.meta.json");
}

function countActiveDevSessions() {
  if (!fs.existsSync(WORKSPACES_DIR)) return 0;

  return fs
    .readdirSync(WORKSPACES_DIR, { withFileTypes: true })
    .filter(d => d.isDirectory())
    .filter(d =>
      fs.existsSync(path.join(WORKSPACES_DIR, d.name, "dev.pid"))
    ).length;
}

async function writeEnvFile(repoDir, envObj) {
  const envPath = path.join(repoDir, ".env");

  const content = Object.entries(envObj || {})
    .map(([k, v]) => `${k}=${String(v).replace(/\n/g, "\\n")}`)
    .join("\n");

  await fse.writeFile(envPath, content, "utf8");
}

function run(cmd, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: "inherit",
      ...options
    });

    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} exited with ${code}`));
    });
  });
}

async function startDev({ siteId }) {
  if (!siteId) throw new Error("siteId required");

  if (fs.existsSync(pidFile(siteId))) {
    throw new Error("Dev Mode already running");
  }

  if (countActiveDevSessions() >= MAX_DEV_SESSIONS) {
    throw new Error("Dev Mode capacity reached");
  }

  const site = await getSiteById(siteId);
  const branch = site.repo_branch || "main";

  const { repoDir } = await cloneOrPull({
    siteId,
    repo: site.repo,
    branch,
    installationId: site.github_installation_id
  });

  const framework = detectFramework(repoDir);
  const port = await findFreePort(40100, 49999);

  await writeEnvFile(repoDir, {
    ...(site.env || {}),
    PORT: port
  });

  // 🔥 FRAMEWORK PATCH (scalable)
  if (framework.framework === "nextjs") {
    patchNextConfig(repoDir);
  }

  await run("npm", ["install"], { cwd: repoDir });

  const basePath = `/__dev/${siteId}`;

  const child = spawn("npm", ["run", "dev"], {
    cwd: repoDir,
    stdio: "inherit",
    env: {
      ...process.env,
      PORT: String(port),
      BASE_PATH: basePath
    }
  });

  fs.writeFileSync(pidFile(siteId), String(child.pid));

  fs.writeFileSync(
    metaFile(siteId),
    JSON.stringify({
      siteId,
      port,
      framework: framework.framework,
      basePath,
      pid: child.pid,
      startedAt: new Date().toISOString()
    }, null, 2)
  );

  console.log(JSON.stringify({
    ok: true,
    siteId,
    port,
    basePath
  }, null, 2));
}

if (require.main === module) {
  startDev({ siteId: process.argv[2] })
    .catch(err => {
      console.error("❌ startDev failed");
      console.error(err.message);
      process.exit(1);
    });
}

module.exports = { startDev };
