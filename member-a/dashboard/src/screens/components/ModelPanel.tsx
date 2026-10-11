import type { ModelInfo } from "../apiTypes";
import { featureLabel, formatLitres, formatNumber, formatUtc } from "../format";
import type { Resource } from "../useResource";
import shared from "./shared.module.css";
import styles from "./ModelPanel.module.css";
import { Badge, ErrorNotice, Spinner } from "./ui";

export const DEFAULT_DISCLAIMER = "Trained and evaluated on synthetic data. Simulation results only; not real-world accuracy.";

/** Relative MAE change of the model against the baseline, as a sentence. Reported honestly either way. */
export function maeComparison(model: ModelInfo): string {
  const modelMae = model.metrics.xgboost.mae_litres;
  const baselineMae = model.metrics.last_observed_baseline.mae_litres;
  if (baselineMae <= 0) return "Baseline error is zero; no comparison available.";
  const change = Math.round(((baselineMae - modelMae) / baselineMae) * 100);
  if (change === 0) return "On the synthetic test split the model's MAE equals the last-observed baseline.";
  return change > 0
    ? `On the synthetic test split the model's MAE is ${change}% lower than the last-observed baseline.`
    : `On the synthetic test split the model's MAE is ${-change}% higher than the last-observed baseline.`;
}

function Details({ model }: { model: ModelInfo }) {
  const rows = [
    { name: "XGBoost", metrics: model.metrics.xgboost },
    { name: "Last observed need (baseline)", metrics: model.metrics.last_observed_baseline },
  ];
  const topImportance = Math.max(0, ...model.feature_importances.map((f) => f.importance));
  return (
    <>
      <p className={shared.muted}>
        <span className={shared.mono}>{model.model_version}</span> · trained {formatUtc(model.trained_at)} ·{" "}
        {formatNumber(model.train_rows)} train rows, {formatNumber(model.test_rows)} test rows · target{" "}
        <span className={shared.mono}>{model.target}</span>
      </p>
      <div className={shared.tableWrap}>
        <table className={shared.table} aria-label="Model error metrics">
          <thead>
            <tr>
              <th>Model</th>
              <th className={shared.num}>MAE</th>
              <th className={shared.num}>RMSE</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <td>{row.name}</td>
                <td className={shared.num}>{formatLitres(row.metrics.mae_litres)}</td>
                <td className={shared.num}>{formatLitres(row.metrics.rmse_litres)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>{maeComparison(model)}</p>
      {model.feature_importances.length > 0 ? (
        <ul className={styles.importances} aria-label="Feature importances">
          {model.feature_importances.map((item) => (
            <li key={item.feature} className={styles.importance}>
              <span className={styles.importanceName}>{featureLabel(item.feature)}</span>
              <span className={styles.importanceTrack}>
                <span
                  className={styles.importanceFill}
                  style={{ width: `${topImportance > 0 ? (item.importance / topImportance) * 100 : 0}%` }}
                />
              </span>
              <span className={styles.importanceValue}>{item.importance.toFixed(2)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className={`${shared.notice} ${shared.noticeWarn}`} data-testid="model-disclaimer">
        {model.disclaimer || DEFAULT_DISCLAIMER}
      </p>
    </>
  );
}

/** Model quality card: XGBoost against the last-observed baseline, always with the simulation disclaimer. */
export function ModelPanel({ model }: { model: Resource<ModelInfo> }) {
  return (
    <section className={shared.card} aria-label="Prediction model">
      <h2 className={shared.cardTitle}>
        Prediction model <Badge tone="warn">Simulation</Badge>
      </h2>
      {model.status === "loading" || model.status === "idle" ? <Spinner label="Loading model details" /> : null}
      {model.status === "error" ? <ErrorNotice error={model.error} what="Model details unavailable" /> : null}
      {model.status === "ready" ? <Details model={model.data} /> : null}
    </section>
  );
}
