import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  createDatabaseClient,
  findMigrationsFolder,
  migrateDatabase,
} from "~/shared/database/client.server";

export async function createTestDatabase() {
  const directory = await mkdtemp(path.join(tmpdir(), "deepresearch-web-test-"));
  const handle = createDatabaseClient(
    `file:${path.join(directory, "test.sqlite")}`,
  );
  await migrateDatabase(handle, findMigrationsFolder());
  return {
    ...handle,
    async cleanup() {
      handle.client.close();
      await rm(directory, { recursive: true, force: true });
    },
  };
}
