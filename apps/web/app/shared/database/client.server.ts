import { createClient } from "@libsql/client";
import { drizzle } from "drizzle-orm/libsql";
import { migrate } from "drizzle-orm/libsql/migrator";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

import * as schema from "./schema";

export function createDatabaseClient(url: string) {
  ensureParentDirectory(url);
  const client = createClient({ url });
  return {
    client,
    db: drizzle(client, { schema }),
  };
}

export type DatabaseHandle = ReturnType<typeof createDatabaseClient>;
export type AppDatabase = DatabaseHandle["db"];

function ensureParentDirectory(url: string) {
  if (!url.startsWith("file:") || url === "file::memory:") {
    return;
  }

  const filePath = url.slice("file:".length).split("?")[0];
  if (!filePath) {
    return;
  }

  const absolutePath = path.isAbsolute(filePath)
    ? filePath
    : path.resolve(process.cwd(), filePath);
  mkdirSync(path.dirname(absolutePath), { recursive: true });
}

export function findMigrationsFolder() {
  const explicit = process.env.DRIZZLE_MIGRATIONS_DIR;
  const candidates = [
    explicit,
    path.resolve(process.cwd(), "drizzle"),
    path.resolve(process.cwd(), "apps/web/drizzle"),
  ].filter((value): value is string => Boolean(value));

  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) {
    throw new Error("Drizzle migrations folder was not found.");
  }
  return found;
}

export async function migrateDatabase(
  handle: DatabaseHandle,
  migrationsFolder = findMigrationsFolder(),
) {
  await migrate(handle.db, { migrationsFolder });
}

let appDatabasePromise: Promise<AppDatabase> | undefined;

export function getAppDatabase() {
  appDatabasePromise ??= (async () => {
    const url =
      process.env.WEB_DATABASE_URL ?? "file:./data/deepresearch-web.sqlite";
    const handle = createDatabaseClient(url);
    await migrateDatabase(handle);
    return handle.db;
  })();
  return appDatabasePromise;
}
