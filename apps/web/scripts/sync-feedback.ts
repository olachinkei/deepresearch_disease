import {
  createDatabaseClient,
  migrateDatabase,
} from "../app/shared/database/client.server";
import { syncFeedbackQueue } from "../app/features/feedback/sync.server";

const databaseUrl =
  process.env.WEB_DATABASE_URL ?? "file:./data/deepresearch-web.sqlite";
const handle = createDatabaseClient(databaseUrl);

try {
  await migrateDatabase(handle);
  const result = await syncFeedbackQueue(handle.db);
  process.stdout.write(`${JSON.stringify(result)}\n`);
} finally {
  handle.client.close();
}
