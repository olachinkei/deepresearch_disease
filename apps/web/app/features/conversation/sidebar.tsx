import {
  BookOpenText,
  Clock3,
  Plus,
} from "lucide-react";

import type { ConversationSummary } from "./view-model";

type ConversationSidebarProps = {
  conversations: ConversationSummary[];
  activeConversationId?: string;
};

function compactDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("ja-JP", {
        month: "numeric",
        day: "numeric",
      }).format(date);
}

export function ConversationSidebar({
  conversations,
  activeConversationId,
}: ConversationSidebarProps) {
  return (
    <aside className="sidebar" aria-label="調査履歴">
      <a className="new-research-button" href="/">
        <Plus aria-hidden size={18} />
        新しい調査
      </a>

      <div className="sidebar-heading">
        <span>調査履歴</span>
        <span className="history-count">{conversations.length}</span>
      </div>

      <nav className="history-list">
        {conversations.length === 0 ? (
          <div className="empty-history">
            <BookOpenText aria-hidden size={22} />
            <p>完了した調査がここに並びます。</p>
          </div>
        ) : (
          conversations.map((conversation) => (
            <a
              className={
                conversation.id === activeConversationId
                  ? "history-item history-item-active"
                  : "history-item"
              }
              href={`/?conversation=${encodeURIComponent(conversation.id)}`}
              key={conversation.id}
            >
              <strong>{conversation.title}</strong>
              <span>
                <Clock3 aria-hidden size={13} />
                {compactDate(conversation.updatedAt)}
              </span>
            </a>
          ))
        )}
      </nav>
    </aside>
  );
}
