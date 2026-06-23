#!/usr/bin/env node

import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");

const FORBIDDEN_PUBLIC_ENV_FRAGMENTS = [
  "API_KEY",
  "SECRET",
  "PASSWORD",
  "DATABASE",
  "RABBITMQ",
  "GROQ",
  "RESEND",
  "RETELL",
  "PRIVATE",
  "WEBHOOK",
];

const SCANNED_EXTENSIONS = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".mjs",
  ".env.example",
]);

const FORBIDDEN_CODE_PATTERNS = [
  /NEXT_PUBLIC_[A-Z0-9_]*(API_KEY|SECRET|PASSWORD|DATABASE|RABBITMQ|GROQ|RESEND|RETELL|PRIVATE|WEBHOOK)[A-Z0-9_]*/g,
];

async function collectFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    if (entry.name === "node_modules" || entry.name === ".next") {
      continue;
    }

    const fullPath = path.join(directory, entry.name);

    if (entry.isDirectory()) {
      files.push(...(await collectFiles(fullPath)));
      continue;
    }

    const extension = path.extname(entry.name);
    const isEnvExample = entry.name === ".env.example";

    if (SCANNED_EXTENSIONS.has(extension) || isEnvExample) {
      files.push(fullPath);
    }
  }

  return files;
}

function checkEnvExample(contents, filePath) {
  const violations = [];

  for (const line of contents.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const [key] = trimmed.split("=", 1);
    if (!key?.startsWith("NEXT_PUBLIC_")) {
      continue;
    }

    const upperKey = key.toUpperCase();
    for (const fragment of FORBIDDEN_PUBLIC_ENV_FRAGMENTS) {
      if (upperKey.includes(fragment)) {
        violations.push(`${filePath}: forbidden public env name ${key}`);
      }
    }
  }

  return violations;
}

function findForbiddenEnvReferences(contents) {
  const violations = [];
  const envReferencePattern = /process\.env\.([A-Z0-9_]+)/g;
  let match = envReferencePattern.exec(contents);

  while (match !== null) {
    const envName = match[1].toUpperCase();

    for (const fragment of FORBIDDEN_PUBLIC_ENV_FRAGMENTS) {
      if (envName.includes(fragment)) {
        violations.push(`forbidden env reference process.env.${match[1]}`);
      }
    }

    match = envReferencePattern.exec(contents);
  }

  return violations;
}

function checkSource(contents, filePath) {
  const violations = [];

  for (const pattern of FORBIDDEN_CODE_PATTERNS) {
    const matches = contents.match(pattern);
    if (matches) {
      for (const match of matches) {
        violations.push(`${filePath}: forbidden env reference ${match}`);
      }
    }
  }

  for (const reference of findForbiddenEnvReferences(contents)) {
    violations.push(`${filePath}: ${reference}`);
  }

  return violations;
}

async function main() {
  const files = await collectFiles(webRoot);
  const violations = [];

  for (const filePath of files) {
    const contents = await readFile(filePath, "utf8");
    const relativePath = path.relative(webRoot, filePath);

    if (path.basename(filePath) === ".env.example") {
      violations.push(...checkEnvExample(contents, relativePath));
    } else if (!relativePath.startsWith(`scripts${path.sep}`)) {
      violations.push(...checkSource(contents, relativePath));
    }
  }

  if (violations.length > 0) {
    console.error("Forbidden frontend env references found:\n");
    for (const violation of violations) {
      console.error(`- ${violation}`);
    }
    process.exit(1);
  }

  console.log("No forbidden frontend env references found.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
