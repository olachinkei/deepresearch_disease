import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "sqlite",
  schema: "./app/shared/database/schema.ts",
  out: "./drizzle",
  dbCredentials: {
    url: process.env.WEB_DATABASE_URL ?? "file:./data/web.sqlite",
  },
  strict: true,
  verbose: true,
});
