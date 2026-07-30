import { z } from "zod";

export const displayNameSchema = z
  .string()
  .trim()
  .min(1, "表示名を入力してください。")
  .max(80, "表示名は80文字以内で入力してください。")
  .refine(
    (value) =>
      Array.from(value).every((character) => {
        const codePoint = character.codePointAt(0) ?? 0;
        return codePoint > 31 && codePoint !== 127;
      }),
    "表示名に制御文字は使用できません。",
  );
