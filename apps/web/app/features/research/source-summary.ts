import { z } from "zod";

const verificationStatusSchema = z.enum([
  "verified",
  "unverified",
  "not_found",
  "failed",
]);

const safeCanonicalUrlSchema = z
  .url()
  .max(2_048)
  .refine((value) => {
    const url = new URL(value);
    return (
      (url.protocol === "https:" || url.protocol === "http:") &&
      !url.username &&
      !url.password
    );
  }, "URL must be credential-free HTTP(S).")
  .transform((value) => {
    const url = new URL(value);
    url.hash = "";
    return url.toString();
  });

export const sourceSummarySchema = z
  .object({
    id: z.string().min(1).max(120),
    title: z.string().trim().min(1).max(300),
    url: safeCanonicalUrlSchema.optional(),
    sourceType: z.enum(["internal", "web"]),
    verificationStatus: verificationStatusSchema.default("unverified"),
  })
  .strict()
  .transform((source) =>
    source.sourceType === "internal"
      ? { ...source, url: undefined }
      : source,
  );

export const sourceSummaryListSchema = z
  .array(sourceSummarySchema)
  .max(12)
  .superRefine((sources, context) => {
    const ids = new Set<string>();
    for (const [index, source] of sources.entries()) {
      if (ids.has(source.id)) {
        context.addIssue({
          code: "custom",
          path: [index, "id"],
          message: "Source IDs must be unique.",
        });
      }
      ids.add(source.id);
    }
  });
