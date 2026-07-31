import {
  ArrowRight,
  Atom,
  CircleCheck,
  FlaskConical,
  LockKeyhole,
} from "lucide-react";
import { useState, type FormEvent } from "react";

import { MECHANISMS } from "~/shared/domain-values";

import {
  researchRequestSchema,
  type ResearchRequest,
} from "./schema";

type ResearchFormProps = {
  displayName?: string;
  busy: boolean;
  onStart: (input: ResearchRequest) => Promise<void>;
};

const mechanismLabels: Record<(typeof MECHANISMS)[number], string> = {
  stabilization: "Stabilization",
  inhibition: "Inhibition",
  degradation: "Degradation",
  activation: "Activation",
  other: "Other",
};

export function ResearchForm({
  displayName,
  busy,
  onStart,
}: ResearchFormProps) {
  const [error, setError] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    const formData = new FormData(event.currentTarget);
    const result = researchRequestSchema.safeParse({
      displayName: String(formData.get("displayName") ?? ""),
      targetMolecule: String(formData.get("targetMolecule") ?? ""),
      mechanism: String(formData.get("mechanism") ?? ""),
      disease: "ischemic stroke",
      researchQuestion: String(formData.get("researchQuestion") ?? ""),
    });
    if (!result.success) {
      setError(result.error.issues[0]?.message ?? "入力内容を確認してください。");
      return;
    }
    await onStart(result.data);
  }

  return (
    <section className="research-hero" aria-labelledby="research-heading">
      <div className="eyebrow">
        <span className="status-dot" />
        PUBLIC CORPUS MVP
      </div>
      <h1 id="research-heading">
        脳梗塞の創薬仮説を、
        <br />
        <span>エビデンスから読み解く。</span>
      </h1>
      <p className="hero-copy">
        公開論文を横断して、標的妥当性、作用機序、臨床移行性、相反する知見を
        引用付きの研究レポートにまとめます。
      </p>

      <form
        aria-describedby={error ? "research-form-error" : undefined}
        className="research-card"
        onSubmit={submit}
      >
        <div className="form-card-header">
          <div className="icon-tile">
            <FlaskConical aria-hidden size={22} />
          </div>
          <div>
            <h2>新しいエビデンス調査</h2>
            <p>任意項目が空でも、脳梗塞の標準調査を開始できます。</p>
          </div>
        </div>

        <div className="form-grid">
          <label className="field field-wide">
            <span className="field-label">
              表示名
              <small>ローカル識別のみ</small>
            </span>
            <div className="input-with-icon">
              <LockKeyhole aria-hidden size={17} />
              <input
                autoComplete="nickname"
                defaultValue={displayName}
                name="displayName"
                placeholder="例：Keisuke"
                readOnly={Boolean(displayName)}
                required
              />
            </div>
          </label>

          <label className="field">
            <span className="field-label">
              Target molecule
              <small>任意・英語</small>
            </span>
            <div className="input-with-icon">
              <Atom aria-hidden size={17} />
              <input
                autoCapitalize="off"
                name="targetMolecule"
                placeholder="例：NLRP3"
              />
            </div>
          </label>

          <label className="field">
            <span className="field-label">
              Mechanism
              <small>任意</small>
            </span>
            <select defaultValue="" name="mechanism">
              <option value="">指定しない</option>
              {MECHANISMS.map((mechanism) => (
                <option key={mechanism} value={mechanism}>
                  {mechanismLabels[mechanism]}
                </option>
              ))}
            </select>
          </label>

          <label className="field field-wide">
            <span className="field-label">
              Disease
              <small>現在の対象疾患</small>
            </span>
            <div className="fixed-field">
              <CircleCheck aria-hidden size={17} />
              <span>Ischemic stroke</span>
              <em>固定</em>
            </div>
          </label>

          <label className="field field-wide">
            <span className="field-label">
              Research question
              <small>任意</small>
            </span>
            <textarea
              name="researchQuestion"
              placeholder="例：NLRP3 inhibition は脳梗塞後の神経炎症を抑え、臨床移行可能な治療仮説になり得るか？"
              rows={4}
            />
          </label>
        </div>

        {error ? (
          <p className="form-error" id="research-form-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="form-card-footer">
          <p>
            社内論文の取り込みと外部送信は、データ管理者の承認まで無効です。
          </p>
          <button className="primary-button" disabled={busy} type="submit">
            {busy ? "調査を開始しています…" : "調査を開始"}
            <ArrowRight aria-hidden size={18} />
          </button>
        </div>
      </form>

      <div className="capability-row" aria-label="調査の特徴">
        <span>01</span>
        <p>
          <strong>論文検索</strong>
          公開コーパスとWeb文献を横断
        </p>
        <span>02</span>
        <p>
          <strong>根拠検証</strong>
          主張と引用の対応を確認
        </p>
        <span>03</span>
        <p>
          <strong>研究レポート</strong>
          限界とnegative evidenceも明示
        </p>
      </div>
    </section>
  );
}
