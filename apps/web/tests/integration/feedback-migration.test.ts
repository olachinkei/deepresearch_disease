import { readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  createDatabaseClient,
  findMigrationsFolder,
} from "~/shared/database/client.server";

describe("feedback migration", () => {
  const databasePath = path.join(
    tmpdir(),
    `deepresearch-feedback-migration-${process.pid}.sqlite`,
  );

  afterEach(async () => {
    await rm(databasePath, { force: true });
  });

  it("archives pre-existing duplicates before adding the unique constraint", async () => {
    const handle = createDatabaseClient(`file:${databasePath}`);
    try {
      const migrations = findMigrationsFolder();
      await executeMigration(handle.client, path.join(migrations, "0000_lush_omega_flight.sql"));
      await handle.client.execute(
        "INSERT INTO local_users (id, display_name) VALUES ('user-1', 'Synthetic User')",
      );
      await handle.client.execute(
        "INSERT INTO conversations (id, user_id, title) VALUES ('conversation-1', 'user-1', 'Synthetic')",
      );
      await handle.client.execute(
        "INSERT INTO turns (id, conversation_id, sequence, status, query) VALUES ('turn-1', 'conversation-1', 1, 'completed', 'Synthetic question')",
      );
      await handle.client.execute(
        "INSERT INTO feedback_queue (id, turn_id, user_id, vote) VALUES ('feedback-a', 'turn-1', 'user-1', 'up')",
      );
      await handle.client.execute(
        "INSERT INTO feedback_queue (id, turn_id, user_id, vote) VALUES ('feedback-b', 'turn-1', 'user-1', 'down')",
      );

      await executeMigration(handle.client, path.join(migrations, "0001_fast_sabra.sql"));

      const current = await handle.client.execute(
        "SELECT id, vote FROM feedback_queue",
      );
      const history = await handle.client.execute(
        "SELECT feedback_id, vote FROM feedback_revisions",
      );
      expect(current.rows).toEqual([
        expect.objectContaining({ id: "feedback-b", vote: "down" }),
      ]);
      expect(history.rows).toEqual([
        expect.objectContaining({ feedback_id: "feedback-a", vote: "up" }),
      ]);
      await expect(
        handle.client.execute(
          "INSERT INTO feedback_queue (id, turn_id, user_id, vote) VALUES ('feedback-c', 'turn-1', 'user-1', 'up')",
        ),
      ).rejects.toThrow(/UNIQUE/u);
    } finally {
      handle.client.close();
    }
  });
});

async function executeMigration(
  client: ReturnType<typeof createDatabaseClient>["client"],
  migrationPath: string,
) {
  const sql = await readFile(migrationPath, "utf8");
  for (const statement of sql.split("--> statement-breakpoint")) {
    if (statement.trim()) {
      await client.execute(statement);
    }
  }
}
