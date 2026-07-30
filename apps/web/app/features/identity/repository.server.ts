import { eq } from "drizzle-orm";

import type { AppDatabase } from "~/shared/database/client.server";
import { localUsers } from "~/shared/database/schema";

export class IdentityRepository {
  constructor(private readonly db: AppDatabase) {}

  async findById(id: string) {
    const [identity] = await this.db
      .select()
      .from(localUsers)
      .where(eq(localUsers.id, id))
      .limit(1);
    return identity;
  }

  async create(input: { id: string; displayName: string }) {
    await this.db.insert(localUsers).values(input);
    const identity = await this.findById(input.id);
    if (!identity) {
      throw new Error("Failed to create local identity.");
    }
    return identity;
  }
}
