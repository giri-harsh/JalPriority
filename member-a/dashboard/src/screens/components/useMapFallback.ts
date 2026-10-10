import { useCallback, useRef, useState } from "react";

/** Tile failures tolerated before the map gives way to the schematic. */
export const TILE_ERROR_LIMIT = 3;

export type MapView = "map" | "schematic";

export interface MapFallback {
  view: MapView;
  /** True when the schematic was chosen automatically because tiles failed. */
  fellBack: boolean;
  /** Call once per failed tile request. */
  reportTileError: () => void;
  /** Manual Map/Schematic choice. Choosing the map again resets the failure count. */
  select: (view: MapView) => void;
}

export function useMapFallback(limit: number = TILE_ERROR_LIMIT): MapFallback {
  const [view, setView] = useState<MapView>("map");
  const [fellBack, setFellBack] = useState(false);
  const errors = useRef(0);

  const reportTileError = useCallback(() => {
    errors.current += 1;
    if (errors.current >= limit) {
      setView("schematic");
      setFellBack(true);
    }
  }, [limit]);

  const select = useCallback((next: MapView) => {
    setView(next);
    if (next === "map") {
      errors.current = 0;
      setFellBack(false);
    }
  }, []);

  return { view, fellBack, reportTileError, select };
}
