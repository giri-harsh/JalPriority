import React, { ReactNode } from "react";
import styles from "../styles/shell.module.css";
import { TabId } from "../contract/types";

interface ScreenSlotProps {
  tabId: TabId;
  children: ReactNode;
}

export const ScreenSlot: React.FC<ScreenSlotProps> = ({ tabId, children }) => {
  return (
    <div
      className={`jpb-screen-slot ${styles.jpbScreenSlot}`}
      data-tab={tabId}
    >
      {children}
    </div>
  );
};
