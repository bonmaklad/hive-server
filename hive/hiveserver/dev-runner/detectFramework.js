const fs = require("fs");
const path = require("path");

function detectFramework(repoDir) {
  const pkgPath = path.join(repoDir, "package.json");

  if (!fs.existsSync(pkgPath)) {
    throw new Error(`package.json not found in ${repoDir}`);
  }

  const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
  const deps = {
    ...(pkg.dependencies || {}),
    ...(pkg.devDependencies || {})
  };

  const has = (name) => Boolean(deps[name]);

  if (has("next")) {
    return {
      framework: "nextjs",
      devCommand: ["npm", ["run", "dev"]]
    };
  }

  if (has("astro")) {
    return {
      framework: "astro",
      devCommand: ["npm", ["run", "dev"]]
    };
  }

  if (has("@remix-run/dev") || has("@remix-run/node")) {
    return {
      framework: "remix",
      devCommand: ["npm", ["run", "dev"]]
    };
  }

  if (has("vite")) {
    return {
      framework: "vite",
      devCommand: ["npm", ["run", "dev"]]
    };
  }

  return {
    framework: "unknown",
    devCommand: ["npm", ["run", "dev"]]
  };
}

module.exports = { detectFramework };
