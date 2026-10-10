import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

const USERS: Record<string, string> = {
  l1: "l1.investigator", l2: "l2.investigator", mlro: "mlro.officer", qa: "qa.reviewer", sme: "sme.reviewer",
  admin: "admin.user",
};

/** Test-user passwords from the repo's .env (written by `sentinel auth bootstrap`). */
function passwords(): Record<string, string> {
  const env = readFileSync(resolve(HERE, "..", "..", "..", ".env"), "utf-8");
  const line = env.split(/\r?\n/).find((l) => l.startsWith("SENTINEL_COGNITO_TEST_USERS="));
  return line ? JSON.parse(line.slice("SENTINEL_COGNITO_TEST_USERS=".length)) : {};
}

/** Sign in as a role's test user: through Cognito's hosted page, or the dev buttons without Cognito. */
export async function signIn(page: Page, role: keyof typeof USERS) {
  await page.goto("/");
  const config = await (await page.request.get("/api/auth/config")).json();
  if (config.mode === "cognito") {
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/amazoncognito\.com/);
    await page.locator('input[name="username"]:visible').fill(USERS[role]);
    await page.locator('input[name="password"]:visible').fill(passwords()[USERS[role]]);
    await page.locator('input[name="signInSubmitButton"]:visible').click();
    await page.waitForURL("http://localhost:5173/**");
  } else {
    await page.getByRole("button", { name: new RegExp(`Sign in as .*`) }).nth(["l1", "l2", "qa", "admin"].indexOf(role)).click();
  }
  await expect(page.getByText(USERS[role]).first()).toBeVisible();
}

export async function openCase(page: Page, caseId: string, visible = true) {
  await page.goto(`/cases/${caseId}`);
  if (visible) await expect(page.getByRole("heading", { name: caseId })).toBeVisible();
}

const LABELS: Record<string, string> = {
  l1: "L1 analyst", l2: "L2 investigator", mlro: "MLRO", qa: "QA reviewer", admin: "Administrator",
};

/** Sign in as a role with one click (demo role switch), from the sign-in page or the header dropdown. */
export async function continueAs(page: Page, role: keyof typeof LABELS) {
  await page.goto("/");
  const roleSwitch = page.getByTestId("role-switch");
  if (await roleSwitch.isVisible().catch(() => false)) {
    await roleSwitch.click();
    await page.getByRole("option", { name: `View as ${LABELS[role]}`, exact: true }).click();
  } else {
    await page.getByRole("button", { name: `Continue as ${LABELS[role]}`, exact: true }).click();
  }
  await expect(page.getByText(USERS[role]).first()).toBeVisible();
}

/** This run's seeded case IDs (fresh per run, see tests/e2e_seed.py). */
export function seeded(): { decide: string; approve: string } {
  return JSON.parse(readFileSync(resolve(HERE, ".cases.json"), "utf-8"));
}