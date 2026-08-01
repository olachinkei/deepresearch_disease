import { expect, test } from "@playwright/test";

async function startResearch(
  page: import("@playwright/test").Page,
  question: string,
) {
  await page.goto("/");
  await page.getByLabel("表示名ローカル識別のみ").fill("Playwright研究者");
  await page.getByLabel("Target molecule任意・英語").fill("NLRP3");
  await page.getByLabel("Mechanism任意").selectOption("inhibition");
  await page.getByLabel("Research question任意").fill(question);
  await page.getByRole("button", { name: "調査を開始" }).click();
}

test("research, streaming, follow-up, reload, and feedback", async ({
  page,
}) => {
  await startResearch(
    page,
    "observe-progress Assess the translational evidence.",
  );

  await expect(page.getByText("公開論文を検索しています。")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible();
  const sourceSummary = page.getByRole("region", {
    name: "構造化ソース概要",
  });
  await expect(sourceSummary.getByText("3件")).toBeVisible();
  await expect(
    sourceSummary.getByRole("link", { name: "Mock publication" }),
  ).toHaveAttribute("href", "https://example.org/paper-1");
  await expect(sourceSummary.getByText("公開")).toBeVisible();
  await expect(sourceSummary.getByText("検証済み")).toBeVisible();

  await page
    .getByRole("textbox", { name: "追加調査", exact: true })
    .fill("Compare only the negative evidence.");
  await page.getByRole("button", { name: "追加調査を送信" }).click();
  await expect(
    page.getByText("Compare only the negative evidence.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("臨床的な有効性は未確立です。").last()).toBeVisible();

  await page.reload();
  const reloadedSourceSummary = page
    .getByRole("region", { name: "構造化ソース概要" })
    .last();
  await expect(reloadedSourceSummary).toContainText("3件");
  await expect(reloadedSourceSummary).toContainText("公開");
  await expect(reloadedSourceSummary).toContainText("検証済み");
  await expect(
    reloadedSourceSummary.getByRole("link", { name: "Mock publication" }),
  ).toHaveAttribute("href", "https://example.org/paper-1");
  await expect(page.getByText("臨床的な有効性は未確立です.")).toHaveCount(0);
  await expect(page.getByText("臨床的な有効性は未確立です。").last()).toBeVisible();

  const [feedbackResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/feedback"),
    ),
    page.getByRole("button", { name: "役に立った" }).last().click(),
  ]);
  const firstFeedback = (await feedbackResponse.json()) as {
    id: string;
    revision: number;
  };
  await expect(
    page.getByText("フィードバックを保存しました").last(),
  ).toBeVisible();

  await page.reload();
  await expect(
    page.getByText(/フィードバックを保存しました.*役に立った.*同期待ち/u).last(),
  ).toBeVisible();

  const revisedFeedback = await page.evaluate(
    async ({ url }) => {
      const response = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          vote: "down",
          reason: "incomplete",
          comment: "Synthetic E2E comment",
        }),
      });
      return {
        status: response.status,
        body: (await response.json()) as { id: string; revision: number },
      };
    },
    { url: feedbackResponse.url() },
  );
  expect(revisedFeedback.status).toBe(200);
  expect(revisedFeedback.body.id).toBe(firstFeedback.id);
  expect(revisedFeedback.body.revision).toBe(firstFeedback.revision + 1);

  await page.reload();
  await expect(
    page
      .getByText(
        /フィードバックを保存しました.*改善が必要.*同期待ち.*コメントあり/u,
      )
      .last(),
  ).toBeVisible();
  await expect(page.getByText("Synthetic E2E comment")).toHaveCount(0);
});

test("cancel and sanitized agent error states", async ({ page }) => {
  await startResearch(page, "slow-cancel");
  await expect(page.getByText("公開論文を検索しています。")).toBeVisible();
  await page.getByRole("button", { name: "キャンセル" }).click();
  await expect(
    page.getByText("調査をキャンセルしました", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("調査をキャンセルしました", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "元の条件で再試行" }).click();
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible();

  await page.getByRole("link", { name: "新しい調査" }).click();
  await page.getByLabel("Target molecule任意・英語").fill("NLRP3");
  await page.getByLabel("Research question任意").fill("agent-error");
  await page.getByRole("button", { name: "調査を開始" }).click();
  await expect(page.getByText("調査エラー", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "元の条件で再試行" }),
  ).toBeVisible();
});

test("retryable error survives reload and retries as a new turn", async ({
  page,
}) => {
  await startResearch(page, "retry-once");
  await expect(page.getByText("調査エラー", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("調査エラー", { exact: true })).toBeVisible();

  const oldTurnId = await page
    .getByText("調査エラー", { exact: true })
    .locator("xpath=ancestor::*[contains(@class, 'turn-recovery')]")
    .getAttribute("data-turn-id");
  await page.getByRole("button", { name: "元の条件で再試行" }).click();
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible();
  const completedTurnId = await page
    .locator("article.message-assistant[data-turn-id]")
    .last()
    .getAttribute("data-turn-id");
  expect(completedTurnId).not.toBe(oldTurnId);
});

test("cancel on a completed turn is idempotent", async ({ page }) => {
  await startResearch(page, "completed-cancel");
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible();
  const turnId = await page
    .locator("article.message-assistant[data-turn-id]")
    .last()
    .getAttribute("data-turn-id");
  expect(turnId).toBeTruthy();

  const result = await page.evaluate(async (id) => {
    const response = await fetch(`/api/turns/${id}/cancel`, {
      method: "POST",
    });
    return (await response.json()) as {
      cancelled: boolean;
      status: string;
    };
  }, turnId);
  expect(result).toMatchObject({
    cancelled: false,
    status: "completed",
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible();
});

test("duplicate SSE frames are ignored without duplicating output", async ({
  page,
}) => {
  await startResearch(page, "duplicate-frame");
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "結論", exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByText("調査サービスに接続できませんでした。"),
  ).toHaveCount(0);
});

for (const question of [
  "truncated-stream",
  "out-of-order",
  "turn-mismatch",
]) {
  test(`SSE protocol violation is retryable and sanitized: ${question}`, async ({
    page,
  }) => {
    await startResearch(page, question);
    await expect(
      page.getByText(
        "調査を完了できませんでした。元の条件で新しいturnとして再試行できます。",
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "元の条件で再試行" }),
    ).toBeVisible();
    await expect(page.getByText("mismatched event")).toHaveCount(0);
  });
}
