import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function expectNoSeriousAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  const violations = results.violations
    .filter(({ impact }) => impact === "critical" || impact === "serious")
    .map(({ id, impact, nodes }) => ({
      id,
      impact,
      targets: nodes.map((node) => node.target),
    }));
  expect(violations).toEqual([]);
}

async function enterResearch(page: Page, question: string) {
  await page.goto("/");
  await page.getByLabel("表示名ローカル識別のみ").fill("A11y研究者");
  await page.getByLabel("Target molecule任意・英語").fill("NLRP3");
  await page.getByLabel("Research question任意").fill(question);
  const start = page.getByRole("button", { name: "調査を開始" });
  await start.focus();
  await page.keyboard.press("Enter");
}

test("critical and serious axe violations remain at zero", async ({ page }) => {
  await page.goto("/");
  await expectNoSeriousAxeViolations(page);

  await page.getByLabel("表示名ローカル識別のみ").fill("Axe研究者");
  await page.getByLabel("Target molecule任意・英語").fill("NLRP3");
  await page.getByRole("button", { name: "調査を開始" }).click();
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible({ timeout: 10_000 });
  await expectNoSeriousAxeViolations(page);
});

test("research controls complete the keyboard flow with scoped announcements", async ({
  page,
}) => {
  await enterResearch(page, "slow-cancel");

  const progress = page.getByRole("status").filter({
    hasText: "公開論文を検索しています。",
  });
  await expect(progress).toBeVisible();
  await expect(progress).toHaveAttribute("aria-live", "polite");
  await expect(page.locator(".transcript")).not.toHaveAttribute("aria-live");

  const cancel = page.getByRole("button", { name: "キャンセル" });
  await cancel.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByText("調査をキャンセルしました", { exact: true }),
  ).toBeVisible();

  const retry = page.getByRole("button", { name: "元の条件で再試行" });
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible({ timeout: 10_000 });

  const source = page.getByRole("link", { name: "Mock publication" }).last();
  await source.focus();
  await expect(source).toBeFocused();

  const followUp = page.getByRole("textbox", {
    name: "追加調査",
    exact: true,
  });
  await followUp.focus();
  await page.keyboard.type("Compare negative evidence.");
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("button", { name: "追加調査を送信" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByText("Compare negative evidence.", { exact: true }),
  ).toBeVisible({ timeout: 10_000 });

  const positiveFeedback = page
    .getByRole("button", { name: "役に立った" })
    .last();
  await positiveFeedback.focus();
  await page.keyboard.press("Enter");
  const feedbackStatus = page
    .getByRole("status")
    .filter({ hasText: "フィードバックを保存しました" })
    .last();
  await expect(feedbackStatus).toBeVisible({ timeout: 10_000 });
  await expect(feedbackStatus).toBeFocused();
});

test("mobile history menu exposes state, moves focus, and returns it on Escape", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const menu = page.getByRole("button", { name: "履歴を開く" });
  await expect(menu).toHaveAttribute("aria-controls", "research-history-sidebar");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await menu.focus();
  await page.keyboard.press("Enter");

  await expect(page.getByRole("button", { name: "履歴を閉じる" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(page.getByRole("link", { name: "新しい調査" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  const focusOutline = await menu.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      style: style.outlineStyle,
      width: Number.parseFloat(style.outlineWidth),
    };
  });
  expect(focusOutline.style).toBe("solid");
  expect(focusOutline.width).toBeGreaterThanOrEqual(3);
  await expect(page.locator("#research-history-sidebar")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  await expectNoSeriousAxeViolations(page);
});

test("reduced motion collapses streaming animation duration", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await enterResearch(page, "slow-cancel");
  await expect(page.getByRole("button", { name: "キャンセル" })).toBeVisible();

  const durationInSeconds = await page.locator(".spin").evaluate((element) => {
    const duration = getComputedStyle(element).animationDuration;
    return duration.endsWith("ms")
      ? Number.parseFloat(duration) / 1000
      : Number.parseFloat(duration);
  });
  expect(durationInSeconds).toBeLessThanOrEqual(0.000_01);
});
