import React from "react";
import { useShell } from "./ShellContext";
import styles from "../styles/shell.module.css";

export const Header: React.FC = () => {
  const { scenarioId, officerId, role, logout } = useShell();

  return (
    <header className={styles.jpbHeader}>
      <div className={styles.jpbHeaderLeft}>
        <h1 className={styles.jpbTitle}>JalPriority AI</h1>
        <span className={styles.jpbDisclaimerBadge}>
          SYNTHETIC DATA · SIMULATION RESULTS
        </span>
      </div>

      <div className={styles.jpbHeaderRight}>
        <div className={`${styles.jpbChip} ${scenarioId ? styles.jpbChipPrimary : ""}`}>
          <span>Scenario:</span>
          <strong>{scenarioId || "None"}</strong>
        </div>

        {officerId && (
          <div className={`${styles.jpbChip} ${styles.jpbOfficerChip}`}>
            <span>Officer:</span>
            <span>{officerId}</span>
            {role && <span className={styles.jpbRoleBadge}>{role}</span>}
          </div>
        )}

        <button
          type="button"
          className={styles.jpbLogoutBtn}
          onClick={logout}
          aria-label="Logout"
        >
          Logout
        </button>
      </div>
    </header>
  );
};
