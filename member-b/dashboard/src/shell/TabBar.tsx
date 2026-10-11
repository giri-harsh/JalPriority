import React from "react";
import { NavLink } from "react-router-dom";
import styles from "../styles/shell.module.css";
import { TabId } from "../contract/types";

interface TabItem {
  id: TabId;
  route: string;
  label: string;
}

const TABS: TabItem[] = [
  { id: "snapshot", route: "/snapshot", label: "Crisis snapshot" },
  { id: "plan", route: "/plan", label: "Dispatch plan" },
  { id: "fairness", route: "/fairness", label: "Fairness & debt" },
  { id: "compare", route: "/compare", label: "Compare" },
  { id: "replan", route: "/replan", label: "Replan" },
];

export const TabBar: React.FC = () => {
  return (
    <nav className={styles.jpbTabBar} aria-label="Main Navigation">
      {TABS.map((tab) => (
        <NavLink
          key={tab.id}
          to={tab.route}
          className={({ isActive }) =>
            `${styles.jpbTab} ${isActive ? styles.jpbTabActive : ""}`
          }
          data-tab={tab.id}
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  );
};
