// Workbench flows end to end (D5-17): browser -> API -> Kafka -> worker -> checkpoint -> browser,
// through the review levels L1 -> L2 -> MLRO (BR-08, BR-16).
import { expect, test, type Page } from "@playwright/test";

import { continueAs, openCase, seeded, signIn } from "./helpers";

// How long the worker may take to apply a step: it can be busy investigating other cases
const WORKER = 120_000;

async function decide(page: Page, action: RegExp, expectMovedOn: boolean) {
  const panel = page.getByTestId("decision-panel");
  await panel.getByRole("radiogroup").getByText(action).click();
  await panel.getByRole("button", { name: "Submit decision" }).click();
  await expect(page.getByText(/Decision sent/)).toBeVisible();
  if (expectMovedOn) await expect(page.getByText(/has moved on to the next level/)).toBeVisible({ timeout: WORKER });
}

test("sign in with Cognito; queue filters survive opening a case", async ({ page }) => {
  await signIn(page, "l1"); // the real Cognito hosted page
  await expect(page.getByTestId("queue")).toBeVisible();
  await page.getByTestId("case-search").fill(seeded().decide.slice(-6));
  await expect(page.getByRole("cell", { name: seeded().decide })).toBeVisible();
  await page.getByRole("cell", { name: seeded().decide }).click();
  await expect(page.getByText("L1 analyst review")).toBeVisible();
  await page.getByTestId("evidence-chip").first().click(); // evidence drawer
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("link", { name: /Queue/ }).click();
  await expect(page.getByTestId("case-search")).toHaveValue(seeded().decide.slice(-6)); // filter kept
});

test("escalation: L1 -> L2 -> MLRO files a SAR, each level seeing only its cases (BR-08, BR-16)", async ({ page }) => {
  const id = seeded().decide;
  await continueAs(page, "l2");
  await openCase(page, id, false); // a fast-lane case starts with L1: L2 cannot see it yet
  await expect(page.getByText(`${id} is not in your queue`)).toBeVisible();

  await continueAs(page, "l1");
  await openCase(page, id);
  await decide(page, /Escalate to L2/, true);

  await continueAs(page, "l2");
  await openCase(page, id);
  await expect(page.getByTestId("decision-trail")).toContainText("Escalate to L2");
  await decide(page, /Escalate to MLRO/, true);

  await continueAs(page, "mlro");
  await openCase(page, id);
  await expect(page.getByText("MLRO review")).toBeVisible();
  await decide(page, /File SAR/, false);
  await expect(page.getByTestId("decided-panel")).toBeVisible({ timeout: WORKER });
  await expect(page.getByText("SAR filed").first()).toBeVisible();
  await expect(page.getByTestId("decision-trail")).toContainText("mlro.officer");
});

test("approve the customer request (L2), request information and attach the reply (L1) (UC-03, UC-04)", async ({ page }) => {
  const id = seeded().approve;
  await continueAs(page, "l2");
  await openCase(page, id);
  const approval = page.getByTestId("approval-panel");
  await expect(approval.getByText("Tipping-off check passed")).toBeVisible();
  await approval.getByRole("button", { name: "Approve" }).click();
  // Approved: the fast-lane case goes back to L1 and leaves L2's view
  await expect(page.getByText(/has moved on to the next level/)).toBeVisible({ timeout: WORKER });

  await continueAs(page, "l1");
  await openCase(page, id);
  await decide(page, /Request information/, false);
  const reply = page.getByTestId("reply-panel");
  await expect(reply).toBeVisible({ timeout: WORKER });
  await reply.getByRole("textbox").fill("The funds are the proceeds of selling my car; the invoice is attached.");
  await reply.getByRole("button", { name: /Attach reply/ }).click();
  await expect(page.getByText(/Reply accepted|the worker is investigating/).first()).toBeVisible({ timeout: WORKER });
});

test("QA review: a QA reviewer labels a case whose automated QA was flagged (UC-05)", async ({ page }) => {
  await continueAs(page, "qa");
  await page.getByRole("button", { name: "QA review", exact: true }).click();
  await expect(page.getByTestId("qa-rubric")).toBeVisible();
  await page.getByRole("button", { name: "Save label" }).click();
  await expect(page.getByText("Label saved")).toBeVisible();
});
