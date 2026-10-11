import { TANKER_STYLE_COUNT, type CoverageClass } from "../mapModel";
import styles from "./maps.module.css";

/** CSS class that colours (and dashes) the polyline of the tanker at `colorIndex`. */
export function tankerClass(colorIndex: number): string {
  return styles[`tanker${colorIndex % TANKER_STYLE_COUNT}`] ?? "";
}

/** CSS classes of an area circle: base shape plus its coverage colour. */
export function areaClass(coverage: CoverageClass): string {
  return `${styles.area} ${styles[`cover_${coverage}`] ?? ""}`;
}
