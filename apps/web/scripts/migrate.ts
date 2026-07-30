import {
  createDatabaseClient,
  migrateDatabase,
} from "../app/shared/database/client.server";

const databaseUrl =
  process.env.WEB_DATABASE_URL ?? "file:./data/deepresearch-web.sqlite";
const handle = createDatabaseClient(databaseUrl);

try {
  await migrateDatabase(handle);
  process.stdout.write(`Migrated ${databaseUrl}\n`);
} finally {
  handle.client.close();
}
