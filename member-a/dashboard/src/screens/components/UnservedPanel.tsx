import type { PlanResult } from "../../contract/types";
import { formatLitres, reasonChipText, unservedReasonText } from "../format";
import shared from "./shared.module.css";
import styles from "./UnservedPanel.module.css";

interface UnservedPanelProps {
  result: PlanResult;
  nameOf: (areaId: string) => string;
}

/** Shortage that the plan could not cover. Always rendered so unmet demand is never hidden. */
export function UnservedPanel({ result, nameOf }: UnservedPanelProps) {
  const unserved = result.unserved;
  const total = unserved.reduce((sum, item) => sum + item.unmet_litres, 0);
  const empty = unserved.length === 0;

  return (
    <section
      className={`${styles.panel} ${empty ? styles.panelClear : styles.panelDanger}`}
      aria-label="Unserved shortage"
      data-testid="unserved-panel"
      data-empty={empty ? "true" : "false"}
    >
      <h2 className={styles.title}>Unserved shortage</h2>
      {empty ? (
        <p className={styles.clearText}>All predicted shortfall covered</p>
      ) : (
        <>
          <p className={styles.total}>
            {formatLitres(total)} unmet across {unserved.length} {unserved.length === 1 ? "area" : "areas"}
          </p>
          <ul className={styles.list}>
            {unserved.map((item) => (
              <li key={item.area_id} className={styles.itemDanger} data-unserved="true" data-area-id={item.area_id}>
                <div className={styles.itemHead}>
                  <span className={styles.itemName}>{nameOf(item.area_id)}</span>
                  <span className={styles.itemLitres}>{formatLitres(item.unmet_litres)}</span>
                </div>
                <div className={`${shared.muted} ${styles.itemReason}`} data-reason-code={item.reason_code}>
                  <strong>{reasonChipText({ code: item.reason_code, params: {} })}.</strong> {unservedReasonText(item.reason_code)}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
