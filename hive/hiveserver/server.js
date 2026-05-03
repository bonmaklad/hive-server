require("dotenv").config({ path: require("path").join(__dirname, ".env") });

const express = require("express");
const fs = require("fs");
const path = require("path");

/* =========================
   IMPORT ENGINES
========================= */

const startDev = require("./dev-runner/startDev");
const stopDev = require("./dev-runner/stopDev");
const gitPush = require("./dev-api/gitPush");

/* =========================
   OPTIONAL AI
========================= */

let runCodex = null;
try {
  runCodex = require("./ai/codex");
} catch (_) {
  // AI not wired yet — safe to ignore
}

/* =========================
   APP SETUP
========================= */

const app = express();
app.use(express.json({ limit: "5mb" }));

const HOST = process.env.HIVESERVER_HOST || "127.0.0.1";
const PORT = Number(process.env.HIVESERVER_PORT || 2999);
const TOKEN = process.env.HIVESERVER_TOKEN;

/* =========================
   AUTH
========================= */

function requireToken(req, res, next) {
  const auth = req.headers.authorization || "";
  if (!TOKEN) {
    return res.status(500).json({ error: "HIVESERVER_TOKEN not set" });
  }
  if (auth !== `Bearer ${TOKEN}`) {
    return res.status(401).json({ error: "unauthorized" });
  }
  next();
}

/* =========================
   HEALTH
========================= */

app.get("/health", (_req, res) => {
  res.json({ ok: true, ts: new Date().toISOString() });
});

/* =========================
   DEV MODE
========================= */

app.post("/dev/start", requireToken, async (req, res) => {
  try {
    const { siteId } = req.body || {};
    if (!siteId) {
      return res.status(400).json({ error: "siteId required" });
    }
    const result = await startDev(siteId);
    res.json(result);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.post("/dev/stop", requireToken, async (req, res) => {
  try {
    const { siteId } = req.body || {};
    if (!siteId) {
      return res.status(400).json({ error: "siteId required" });
    }
    const result = await stopDev(siteId);
    res.json(result);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.get("/dev/status", requireToken, (req, res) => {
  try {
    const siteId = req.query.siteId;
    if (!siteId) {
      return res.status(400).json({ error: "siteId required" });
    }

    const workspace = path.join("/opt/hive/workspaces", siteId);
    const metaPath = path.join(workspace, "dev.meta.json");

    if (!fs.existsSync(metaPath)) {
      return res.json({ ok: true, running: false });
    }

    const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
    res.json({
      ok: true,
      running: true,
      siteId,
      meta,
      previewUrl: `/__dev/${siteId}`,
    });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});


/* =========================
   GIT
========================= */

app.post("/dev/git/push", requireToken, (req, res) => {
  try {
    const { siteId, message } = req.body || {};
    if (!siteId) {
      return res.status(400).json({ error: "siteId required" });
    }
    const result = gitPush(siteId, message);
    res.json(result);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

/* =========================
   AI (OPTIONAL)
========================= */

app.post("/ai/codex", requireToken, async (req, res) => {
  if (!runCodex) {
    return res.status(501).json({ error: "AI not configured" });
  }

  try {
    const { siteId, path: filePath, instruction, content } = req.body || {};
    if (!siteId || !filePath || !instruction || typeof content !== "string") {
      return res.status(400).json({ error: "invalid payload" });
    }
    const diff = await runCodex({ siteId, path: filePath, instruction, content });
    res.json({ diff });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

/* =========================
   LISTEN
========================= */

app.listen(PORT, HOST, () => {
  console.log(`🧠 HiveServer listening on http://${HOST}:${PORT}`);
});
