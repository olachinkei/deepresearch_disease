import { ExternalLink, LibraryBig } from "lucide-react";

import type { AssistantMessageMetadata } from "./message-metadata";

const VERIFICATION_LABELS = {
  verified: "検証済み",
  unverified: "未検証",
  not_found: "書誌情報未確認",
  failed: "検証失敗",
} as const;

export function SourceSummary({
  metadata,
}: {
  metadata?: AssistantMessageMetadata;
}) {
  if (!metadata?.sourceCount && !metadata?.sourceSummary?.length) {
    return null;
  }
  const sources = metadata.sourceSummary ?? [];
  const sourceCount = metadata.sourceCount ?? sources.length;

  return (
    <section className="source-summary" aria-label="構造化ソース概要">
      <header>
        <h3>
          <LibraryBig aria-hidden size={15} />
          ソース概要
        </h3>
        <span>{sourceCount}件</span>
      </header>
      {sources.length ? (
        <ul>
          {sources.map((source) => (
            <li key={source.id}>
              <div className="source-summary-title">
                {source.sourceType === "web" && source.url ? (
                  <a href={source.url} rel="noreferrer" target="_blank">
                    {source.title}
                    <ExternalLink aria-hidden size={12} />
                  </a>
                ) : (
                  <span>{source.title}</span>
                )}
              </div>
              <div className="source-summary-badges">
                <span
                  className={`source-kind source-kind-${source.sourceType}`}
                >
                  {source.sourceType === "internal" ? "内部" : "公開"}
                </span>
                <span
                  className={`verification verification-${source.verificationStatus}`}
                >
                  {VERIFICATION_LABELS[source.verificationStatus]}
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p>ソース件数のみ記録されています。</p>
      )}
    </section>
  );
}
