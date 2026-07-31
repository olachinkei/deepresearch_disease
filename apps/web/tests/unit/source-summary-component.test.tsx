// @vitest-environment happy-dom

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SourceSummary } from "~/features/conversation/source-summary";

describe("SourceSummary", () => {
  it("shows public/internal classification without linking internal sources", () => {
    render(
      <SourceSummary
        metadata={{
          schemaVersion: "1.0",
          sourceCount: 2,
          sourceSummary: [
            {
              id: "E1",
              title: "Verified public paper",
              url: "https://example.org/paper",
              sourceType: "web",
              verificationStatus: "verified",
            },
            {
              id: "I1",
              title: "Synthetic internal record",
              sourceType: "internal",
              verificationStatus: "unverified",
            },
          ],
        }}
      />,
    );

    const region = screen.getByRole("region", { name: "構造化ソース概要" });
    expect(within(region).getByText("2件")).toBeTruthy();
    expect(
      within(region)
        .getByRole("link", { name: "Verified public paper" })
        .getAttribute("href"),
    ).toBe("https://example.org/paper");
    expect(within(region).getByText("公開")).toBeTruthy();
    expect(within(region).getByText("検証済み")).toBeTruthy();
    expect(within(region).getByText("内部")).toBeTruthy();
    expect(within(region).getByText("未検証")).toBeTruthy();
    expect(
      within(region).queryByRole("link", {
        name: "Synthetic internal record",
      }),
    ).toBeNull();
  });
});
