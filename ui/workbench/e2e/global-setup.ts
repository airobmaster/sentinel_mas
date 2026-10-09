// Seeds the test cases (tests/e2e_seed.py) in the stack's Postgres before the browser tests run.
import { execFileSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

export const REPO = resolve(HERE, "..", "..", "..");

export default function globalSetup() {
  const python = [resolve(REPO, ".svenv", "Scripts", "python.exe"), resolve(REPO, ".svenv", "bin", "python")]
    .find(existsSync) ?? "python";
  const out = execFileSync(python, ["-m", "tests.e2e_seed", "seed"], { cwd: REPO, encoding: "utf-8" });
  const cases = out.trim().split("\n").pop()!;
  writeFileSync(resolve(HERE, ".cases.json"), cases); // this run's case IDs, read by the tests
  console.log(`seeded: ${cases}`);
}
