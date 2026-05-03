const httpProxy = require("http-proxy");
const express = require("express");
const fs = require("fs");
const path = require("path");
const { createClient } = require("@supabase/supabase-js");

/* =========================
   DEV API IMPORTS
========================= */

const listFiles = require("/opt/hive/hiveserver/dev-api/listFiles");
const readFile = require("/opt/hive/hiveserver/dev-api/readFile");
const writeFile = require("/opt/hive/hiveserver/dev-api/writeFile");
const applyDiff = require("/opt/hive/hiveserver/dev-api/applyDiff");
const gitPush = require("/opt/hive/hiveserver/dev-api/gitPush");

/* =========================
   PROXY
========================= */

const proxy = httpProxy.createProxyServer({
  ws: true,
  proxyTimeout: 30000,
  timeout: 30000,
});

proxy.on("error", (err, req, res) => {
  console.error("Proxy error:", err.message);
  if (!res.headersSent) {
    res.writeHead(502, { "Content-Type": "text/plain" });
  }
  res.end("Bad gateway");
});

/* =========================
   APP
========================= */

const app = express();

/* =========================
   SUPABASE
========================= */

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

/* =========================
   HELPERS
========================= */

function isStaticFramework(framework) {
  return [
    "gatsby",
    "static",
    "vue-cli",
    "vue-vite",
    "vite",
    "react-cra"
  ].includes(framework);
}

function safeJoin(base, target) {
  const safePath = path.normalize(target).replace(/^(\.\.[\/\\])+/, "");
  return path.join(base, safePath);
}

/* =========================
   DEV FILE + GIT APIs
========================= */

app.get("/api/dev/files", (req, res) => {
  try {
    res.json(listFiles(req.query.siteId));
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.get("/api/dev/file", (req, res) => {
  try {
    res.json({
      content: readFile(req.query.siteId, req.query.path),
    });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.post("/api/dev/file", express.json(), (req, res) => {
  try {
    const { siteId, path: filePath, content, hash } = req.body;
    const result = writeFile(siteId, filePath, content, hash);
    res.json(result);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.post("/api/dev/apply-diff", express.json(), (req, res) => {
  try {
    const { siteId, path: filePath, diff } = req.body;
    const result = applyDiff(siteId, filePath, diff);
    res.json(result);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.post("/api/dev/git/push", express.json(), (req, res) => {
  try {
    const { siteId, message } = req.body;
    const result = gitPush(siteId, message);
    res.json(result);
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

/* =========================
   DEV MODE PROXY
========================= */

app.use(async (req, res, next) => {
  if (!req.url.startsWith("/__dev/")) {
    return next();
  }

  const [, , siteId] = req.url.split("/");
  if (!siteId) return res.status(404).send("Invalid dev URL");

  const workspace = path.join("/opt/hive/workspaces", siteId);
  const metaPath = path.join(workspace, "dev.meta.json");

  if (!fs.existsSync(metaPath)) {
    return res.status(404).send("Dev mode not running");
  }

  const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));

  fs.writeFileSync(
    path.join(workspace, "last_access"),
    Date.now().toString()
  );

  proxy.web(req, res, {
    target: `http://127.0.0.1:${meta.port}`,
    changeOrigin: true,
    ws: true,
  });
});

/* =========================
   MAIN SITE ROUTER
========================= */

app.use(async (req, res) => {
  const host = req.headers.host?.split(":")[0];
  if (!host) return res.status(400).send("No host header");

  let site = null;
  let siteId = null;

  // Preview domain
  if (host.endsWith(".hivehq.nz")) {
    siteId = host.replace(".hivehq.nz", "");
    const { data } = await supabase
      .from("sites")
      .select("id, port, framework")
      .eq("id", siteId)
      .single();
    site = data;
  }

  // Custom domain
  if (!site) {
    const { data } = await supabase
      .from("sites")
      .select("id, port, framework")
      .eq("domain", host)
      .single();
    site = data;
    siteId = data?.id;
  }

  if (!site) return res.status(404).send("Site not found");

  /* ===== STATIC SITE ===== */

  if (isStaticFramework(site.framework)) {
    const releasePath = path.join("/srv/sites", siteId, "current");
    let filePath = safeJoin(releasePath, req.path);

    if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
      filePath = path.join(filePath, "index.html");
    }

    if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
      return res.sendFile(filePath);
    }

    return res.sendFile(path.join(releasePath, "index.html"));
  }

  /* ===== NODE / NEXT SITE ===== */

  if (site.port) {
    return proxy.web(req, res, {
      target: `http://127.0.0.1:${site.port}`,
      changeOrigin: true,
      ws: true,
    });
  }

  return res.status(500).send("Invalid site configuration");
});

/* =========================
   SERVER + WS
========================= */

const server = app.listen(8080, () => {
  console.log("🧭 Hive router listening on 8080");
});

server.on("upgrade", (req, socket, head) => {
  proxy.ws(req, socket, head);
});
