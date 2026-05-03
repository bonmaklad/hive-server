const fs = require("fs");
const https = require("https");
const jwt = require("jsonwebtoken");

const GITHUB_APP_ID = process.env.GITHUB_APP_ID || "2479277";
const PRIVATE_KEY_PATH = "/opt/hive-deploy/github-app.pem";

function loadPrivateKey() {
  if (!fs.existsSync(PRIVATE_KEY_PATH)) {
    throw new Error(`GitHub App private key not found at ${PRIVATE_KEY_PATH}`);
  }
  return fs.readFileSync(PRIVATE_KEY_PATH, "utf8");
}

function createJWT() {
  const now = Math.floor(Date.now() / 1000);

  return jwt.sign(
    {
      iat: now - 60,
      exp: now + 600,
      iss: GITHUB_APP_ID
    },
    loadPrivateKey(),
    { algorithm: "RS256" }
  );
}

function request(options, body) {
  return new Promise((resolve, reject) => {
    const req = https.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(JSON.parse(data));
        } else {
          reject(new Error(`GitHub API ${res.statusCode}: ${data}`));
        }
      });
    });

    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function getInstallationToken(installationId) {
  if (!installationId) {
    throw new Error("github_installation_id is required");
  }

  const jwtToken = createJWT();

  const res = await request({
    hostname: "api.github.com",
    path: `/app/installations/${installationId}/access_tokens`,
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwtToken}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "hive-dev-runner"
    }
  });

  return res.token;
}

module.exports = { getInstallationToken };
