const express = require("express");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const jwt = require("jsonwebtoken");
const axios = require("axios");
const { createClient } = require("@supabase/supabase-js");

/* =========================
   SUPABASE
========================= */
const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

/* =========================
   APP CONFIG
========================= */
const app = express();
const PORT = 3000;

const WEBHOOK_SECRET = process.env.GITHUB_WEBHOOK_SECRET;
const APP_ID = process.env.GITHUB_APP_ID;
const PRIVATE_KEY_PATH = process.env.GITHUB_APP_PRIVATE_KEY_PATH;

const BUILD_ROOT = "/srv/builds";
const SITES_ROOT = "/srv/sites"; // /srv/sites/<id>/releases/<releaseId> + current -> release

/* =========================
   ENV FILE HANDLING
========================= */
function writeEnvFile(site) {
  const envDir = "/etc/hive-sites";
  const envPath = `${envDir}/${site.id}.env`;

  fs.mkdirSync(envDir, { recursive: true });

  const envVars = site.env || {};
  const content = Object.entries(envVars)
    .map(([k, v]) => `${k}=${String(v).replace(/\n/g, "\\n")}`)
    .join("\n");

  fs.writeFileSync(envPath, content, { mode: 0o600 });
  return envPath;
}

/* =========================
   DEPLOYMENT LOGGING (Supabase)
========================= */
async function logLine(deploymentId, stream, message) {
  try {
    await supabase.from("deployment_logs").insert({
      deployment_id: deploymentId,
      stream,
      message: String(message || "").slice(0, 8000),
    });
  } catch (e) {
    // Don't break deploy on log failures; still emit to stdout
    console.error("⚠️ logLine failed:", e?.message || e);
  }
}

async function run(cmd, cwd, deploymentId) {
  await logLine(deploymentId, "system", `▶ ${cmd}`);
  // echo to service logs too (so journalctl shows something useful)
  console.log(`▶ ${cmd}`);

  return new Promise((resolve, reject) => {
    const child = spawn(cmd, {
      cwd,
      shell: true,
      env: process.env,
    });

    child.stdout.on("data", (d) => {
      const s = d.toString();
      process.stdout.write(s);
      logLine(deploymentId, "stdout", s);
    });

    child.stderr.on("data", (d) => {
      const s = d.toString();
      process.stderr.write(s);
      logLine(deploymentId, "stderr", s);
    });

    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Command failed (${code}): ${cmd}`));
    });
  });
}

/* =========================
   DB HELPERS
========================= */
async function getSiteByRepo(repo) {
  const { data, error } = await supabase
    .from("sites")
    .select("*")
    .eq("repo", repo)
    .single();

  if (error) throw new Error("Site not found");
  return data;
}

/**
 * Atomic deploy layout:
 * /srv/sites/<id>/
 *   releases/<releaseId>/
 *   current -> releases/<releaseId>
 */
function sitePaths(siteId) {
  const base = path.join(SITES_ROOT, String(siteId));
  return {
    base,
    releases: path.join(base, "releases"),
    current: path.join(base, "current"),
  };
}

function ensureSiteDirs(siteId) {
  const p = sitePaths(siteId);
  fs.mkdirSync(p.releases, { recursive: true });
  return p;
}

function releaseIdNow() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function acquireDeployLock(siteId) {
  const lockPath = `/tmp/hive-deploy-${siteId}.lock`;
  try {
    const fd = fs.openSync(lockPath, "wx");
    fs.writeFileSync(fd, `${Date.now()}`);
    fs.closeSync(fd);
  } catch (e) {
    throw new Error(`Deploy already running for site ${siteId}`);
  }
  return lockPath;
}

function releaseDeployLock(lockPath) {
  try {
    fs.unlinkSync(lockPath);
  } catch (_) {}
}

async function atomicSwitchCurrent(siteId, newReleasePath, deploymentId) {
  const p = sitePaths(siteId);
  await run(`ln -sfn ${newReleasePath} ${p.current}`, "/", deploymentId);
}

async function createOrUpdateSystemd(site, startCmd, deploymentId) {
  const serviceName = `hive-site-${site.id}`;
  const p = sitePaths(site.id);
  const envPath = writeEnvFile(site);

  await run(
    `sudo /usr/local/bin/hive-systemd.sh ${serviceName} ${p.current} "${startCmd}" ${envPath}`,
    "/",
    deploymentId
  );
}

/* =========================
   STATIC DETECTION + BUILD STRATEGY
========================= */

function safeReadJson(filePath) {
  try {
    if (!fs.existsSync(filePath)) return null;
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_) {
    return null;
  }
}

function hasAnyFile(buildDir, names) {
  return names.some((n) => fs.existsSync(path.join(buildDir, n)));
}

function detectFrameworkFromPackageJson(buildDir, siteFrameworkRaw) {
  // If site explicitly declares something usable, we respect it (but still infer missing bits)
  const siteFramework = (siteFrameworkRaw || "").toLowerCase();

  // Special: if DB says "static", treat as prebuilt static
  if (siteFramework === "static") {
    return { kind: "static-prebuilt" };
  }

  const pkgPath = path.join(buildDir, "package.json");
  const pkg = safeReadJson(pkgPath);

  // If no package.json, assume prebuilt static (html/css/js)
  if (!pkg) return { kind: "static-prebuilt" };

  const deps = {
    ...(pkg.dependencies || {}),
    ...(pkg.devDependencies || {}),
  };
  const scripts = pkg.scripts || {};

  // NEXT
  if (deps.next || scripts.build?.includes("next")) {
    return { kind: "next" };
  }

  // GATSBY
  if (deps.gatsby || scripts.build?.includes("gatsby")) {
    return { kind: "gatsby" };
  }

  // VUE (Vue CLI or Vite Vue)
  if (deps["@vue/cli-service"] || scripts.build?.includes("vue-cli-service")) {
    return { kind: "vue-cli" };
  }
  if (deps.vue && (deps.vite || scripts.build?.includes("vite"))) {
    // could be vue+vite
    return { kind: "vue-vite" };
  }

  // REACT (CRA)
  if (deps["react-scripts"] || scripts.build?.includes("react-scripts")) {
    return { kind: "react-cra" };
  }

  // VITE (generic)
  if (deps.vite || scripts.build?.includes("vite")) {
    return { kind: "vite" };
  }
 
  // NODE (generic server)
  // If it has a start script but no obvious static framework, treat as node.
  if (scripts.start) {
    return { kind: "node" };
  }

  // default: static-prebuilt (e.g. repo of built files, or no scripts)
  return { kind: "static-prebuilt" };
}

function outputDirFor(kind) {
  // Where built static assets usually end up
  switch (kind) {
    case "gatsby":
      return "public";
    case "react-cra":
      return "build";
    case "vite":
    case "vue-vite":
      return "dist";
    case "vue-cli":
      return "dist";
    default:
      return null;
  }
}

function nodeStartCmd(site, buildDir, kind) {
  // systemd service runs from /srv/sites/<id>/current (symlink), so startCmd should be relative/portable
  // If user has a custom start command in DB, use it.
  if (site.start_cmd && String(site.start_cmd).trim()) return String(site.start_cmd).trim();

  // NEXT standalone: use server.js inside standalone output. Your previous code expects output:'standalone'
  if (kind === "next") {
    return `PORT=${site.port} node server.js`;
  }

  // Generic node: prefer npm start if present
  return `PORT=${site.port} npm start`;
}

/* =========================
   DEPLOY PIPELINE
========================= */
async function deployRepo(site, token) {
  const lockPath = acquireDeployLock(site.id);

  // Create deployment row FIRST
  const { data: deployment, error: depErr } = await supabase
    .from("deployments")
    .insert({
      site_id: site.id,
      commit_sha: null,
      status: "pending",
    })
    .select()
    .single();

  if (depErr || !deployment?.id) {
    releaseDeployLock(lockPath);
    throw new Error(`Failed to create deployment row: ${depErr?.message || "unknown"}`);
  }

  const deploymentId = deployment.id;

  try {
    const buildDir = path.join(BUILD_ROOT, String(site.id));
    const p = ensureSiteDirs(site.id);

    const releaseId = releaseIdNow();
    const releaseDir = path.join(p.releases, releaseId);

    const gitUrl = `https://x-access-token:${token}@github.com/${site.repo}.git`;

    fs.mkdirSync(buildDir, { recursive: true });
    fs.mkdirSync(releaseDir, { recursive: true });

    // Clone/update in buildDir
    if (!fs.existsSync(path.join(buildDir, ".git"))) {
      await run(`git clone ${gitUrl} .`, buildDir, deploymentId);
    } else {
      await run(`git fetch`, buildDir, deploymentId);
      await run(`git reset --hard origin/main`, buildDir, deploymentId);
    }

    // Capture commit sha
    try {
      const sha = require("child_process")
        .execSync("git rev-parse HEAD", { cwd: buildDir })
        .toString()
        .trim();
      await supabase.from("deployments").update({ commit_sha: sha }).eq("id", deploymentId);
    } catch (_) {}

    const envPath = writeEnvFile(site);

    // Auto detect framework if needed
    const detected = detectFrameworkFromPackageJson(buildDir, site.framework);
    const kind = detected.kind;

    await logLine(deploymentId, "system", `Detected framework: ${kind} (site.framework=${site.framework || "null"})`);

    // ===== STATIC PREBUILT (no build) =====
    if (kind === "static-prebuilt") {
      // Copy entire repo contents (excluding .git) to releaseDir
      await run(`rsync -a --delete --exclude .git ${buildDir}/ ${releaseDir}/`, "/", deploymentId);

      // Optional sanity: ensure index.html exists, otherwise warn (don’t fail)
      if (!fs.existsSync(path.join(releaseDir, "index.html"))) {
        await logLine(
          deploymentId,
          "system",
          "⚠️ static-prebuilt: index.html not found at release root. Your router must handle this or your site won’t render."
        );
      }

      await atomicSwitchCurrent(site.id, releaseDir, deploymentId);

      await supabase.from("deployments").update({ status: "success" }).eq("id", deploymentId);
      console.log(`🚀 Deployed (static-prebuilt) ${site.domain} -> ${releaseDir}`);
      return;
    }

    // ===== NODE / STATIC BUILDS (npm) =====
    // Clean caches (best-effort)
    await run(`rm -rf .next/cache`, buildDir, deploymentId).catch(() => {});

    // Install + build with build-time env
    await run(`bash -lc "set -a && source ${envPath} && npm ci"`, buildDir, deploymentId);
    // If no build script, treat as node-only (npm start)
    const pkg = safeReadJson(path.join(buildDir, "package.json")) || {};
    const scripts = pkg.scripts || {};
    const hasBuildScript = !!scripts.build;

    if (hasBuildScript) {
      await run(`bash -lc "set -a && source ${envPath} && npm run build"`, buildDir, deploymentId);
    } else {
      await logLine(deploymentId, "system", "No build script found; treating as node app (start only).");
    }

    // ===== NEXT special handling (standalone) =====
    if (kind === "next") {
      const standaloneDir = path.join(buildDir, ".next", "standalone");
      const staticDir = path.join(buildDir, ".next", "static");
      const publicDir = path.join(buildDir, "public");

      if (!fs.existsSync(standaloneDir)) {
        throw new Error(
          "Next standalone output not found. Add next.config.js { output: 'standalone' } then redeploy."
        );
      }

      await run(`rsync -a ${standaloneDir}/ ${releaseDir}/`, "/", deploymentId);

      fs.mkdirSync(path.join(releaseDir, ".next"), { recursive: true });
      if (fs.existsSync(staticDir)) {
        await run(
          `rsync -a --delete ${staticDir}/ ${path.join(releaseDir, ".next", "static")}/`,
          "/",
          deploymentId
        );
      }

      if (fs.existsSync(publicDir)) {
        await run(`rsync -a --delete ${publicDir}/ ${path.join(releaseDir, "public")}/`, "/", deploymentId);
      }

      await atomicSwitchCurrent(site.id, releaseDir, deploymentId);

      const startCmd = nodeStartCmd(site, buildDir, kind);
      await createOrUpdateSystemd(site, startCmd, deploymentId);

      await supabase.from("deployments").update({ status: "success" }).eq("id", deploymentId);
      console.log(`🚀 Deployed (next) ${site.domain} -> ${releaseDir}`);
      return;
    }

    // ===== Static frameworks built output =====
    const outDir = outputDirFor(kind);
    if (outDir) {
      // copy only built output into release
      if (!fs.existsSync(path.join(buildDir, outDir))) {
        // fallback: if output missing, treat as node (or warn)
        await logLine(
          deploymentId,
          "system",
          `⚠️ Expected output dir "${outDir}" not found. Falling back to node-style deploy (copy full repo).`
        );
        await run(`rsync -a --delete --exclude .git ${buildDir}/ ${releaseDir}/`, "/", deploymentId);

        await atomicSwitchCurrent(site.id, releaseDir, deploymentId);
        const startCmd = nodeStartCmd(site, buildDir, "node");
        await createOrUpdateSystemd(site, startCmd, deploymentId);

        await supabase.from("deployments").update({ status: "success" }).eq("id", deploymentId);
        console.log(`🚀 Deployed (fallback node) ${site.domain} -> ${releaseDir}`);
        return;
      }

      await run(`rsync -a --delete ${outDir}/ ${releaseDir}/`, buildDir, deploymentId);
      await atomicSwitchCurrent(site.id, releaseDir, deploymentId);

      await supabase.from("deployments").update({ status: "success" }).eq("id", deploymentId);
      console.log(`🚀 Deployed (${kind}) ${site.domain} -> ${releaseDir}`);
      return;
    }

    // ===== Generic NODE deploy (copy built repo into release + systemd) =====
    await run(`rsync -a --delete --exclude .git ${buildDir}/ ${releaseDir}/`, "/", deploymentId);
    await atomicSwitchCurrent(site.id, releaseDir, deploymentId);

    const startCmd = nodeStartCmd(site, buildDir, "node");
    await createOrUpdateSystemd(site, startCmd, deploymentId);

    await supabase.from("deployments").update({ status: "success" }).eq("id", deploymentId);
    console.log(`🚀 Deployed (node) ${site.domain} -> ${releaseDir}`);
  } catch (err) {
    await logLine(deploymentId, "stderr", `❌ Deploy failed: ${err.message}`);
    await supabase.from("deployments").update({ status: "failed" }).eq("id", deploymentId);
    throw err;
  } finally {
    releaseDeployLock(lockPath);
  }
}

/* =========================
   WEBHOOK
========================= */
app.use(
  express.json({
    verify: (req, res, buf) => {
      req.rawBody = buf;
    },
  })
);

function verifySignature(req) {
  const sig = req.headers["x-hub-signature-256"];
  if (!sig) return false;

  const hmac = crypto.createHmac("sha256", WEBHOOK_SECRET);
  const digest = "sha256=" + hmac.update(req.rawBody).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(digest));
}

async function getInstallationToken(installationId) {
  const now = Math.floor(Date.now() / 1000);
  const jwtToken = jwt.sign(
    { iat: now - 60, exp: now + 600, iss: APP_ID },
    fs.readFileSync(PRIVATE_KEY_PATH),
    { algorithm: "RS256" }
  );

  const res = await axios.post(
    `https://api.github.com/app/installations/${installationId}/access_tokens`,
    {},
    {
      headers: {
        Authorization: `Bearer ${jwtToken}`,
        Accept: "application/vnd.github+json",
      },
    }
  );

  return res.data.token;
}

app.post("/webhooks/github", async (req, res) => {
  try {
    if (!verifySignature(req)) return res.sendStatus(401);
    if (req.headers["x-github-event"] !== "push") return res.sendStatus(200);
    if (req.body.ref !== "refs/heads/main") return res.sendStatus(200);

    const site = await getSiteByRepo(req.body.repository.full_name);
    const token = await getInstallationToken(req.body.installation.id);

    await deployRepo(site, token);
    res.send("Deployed");
  } catch (err) {
    console.error("❌ Deploy failed:", err);
    res.status(500).send(`Deploy failed: ${err.message}`);
  }
});

app.listen(PORT, () => {
  console.log(`🚀 Hive Deploy listening on port ${PORT}`);
});
