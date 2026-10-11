import { latLngBounds } from "leaflet";
import "leaflet/dist/leaflet.css";
import { CircleMarker, MapContainer, Polyline, Rectangle, TileLayer, Tooltip } from "react-leaflet";
import { stopLabel, type MapModel } from "../mapModel";
import { areaClass, tankerClass } from "./mapStyles";
import styles from "./maps.module.css";

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = "© OpenStreetMap contributors";
/** Half-side of the depot square in degrees of latitude (about 100 m). */
const DEPOT_HALF_SIDE_DEG = 0.0009;
const RING_GAP_PX = 5;

interface RouteMapProps {
  model: MapModel;
  /** Called for every tile that fails to load. */
  onTileError: () => void;
}

/**
 * Leaflet map of the plan. Uses only vector layers (CircleMarker, Polyline, Rectangle),
 * never marker icons, so no image assets are needed besides the tiles.
 *
 * Path styling is passed as direct props rather than `pathOptions`: Leaflet applies
 * `className` only when a layer is constructed.
 */
export function RouteMap({ model, onTileError }: RouteMapProps) {
  const points: [number, number][] = [[model.depot.lat, model.depot.lon], ...model.areas.map((a): [number, number] => [a.lat, a.lon])];
  const bounds = latLngBounds(points);
  const lonHalfSide = DEPOT_HALF_SIDE_DEG / Math.cos((model.depot.lat * Math.PI) / 180);

  return (
    <MapContainer
      className={styles.leaflet}
      bounds={bounds}
      boundsOptions={{ padding: [48, 48] }}
      scrollWheelZoom={false}
      attributionControl
    >
      <TileLayer url={TILE_URL} attribution={ATTRIBUTION} maxZoom={19} eventHandlers={{ tileerror: onTileError }} />

      {model.routes.map((route) => (
        <Polyline
          key={route.tankerId}
          positions={route.nodes.map((node): [number, number] => [node.lat, node.lon])}
          className={`${styles.route} ${tankerClass(route.colorIndex)}`}
          weight={4}
        />
      ))}

      {model.areas.map((area) => (
        <CircleMarker
          key={area.id}
          center={[area.lat, area.lon]}
          radius={area.radius}
          className={areaClass(area.coverageClass)}
          weight={2}
          fillOpacity={0.6}
        >
          <Tooltip permanent direction="top" offset={[0, -area.radius]} className={styles.tip}>
            <span className={styles.tipName}>{area.name}</span>
            {area.stops.length > 0 ? <span className={styles.tipStops}>{stopLabel(area)}</span> : null}
          </Tooltip>
        </CircleMarker>
      ))}

      {model.areas
        .filter((area) => area.unmet > 0)
        .map((area) => (
          <CircleMarker
            key={`ring-${area.id}`}
            center={[area.lat, area.lon]}
            radius={area.radius + RING_GAP_PX}
            interactive={false}
            className={styles.ring}
            weight={3}
            fill={false}
          />
        ))}

      <Rectangle
        bounds={[
          [model.depot.lat - DEPOT_HALF_SIDE_DEG, model.depot.lon - lonHalfSide],
          [model.depot.lat + DEPOT_HALF_SIDE_DEG, model.depot.lon + lonHalfSide],
        ]}
        className={styles.depot}
        weight={2}
        fillOpacity={1}
      >
        <Tooltip direction="bottom">{model.depot.name}</Tooltip>
      </Rectangle>
    </MapContainer>
  );
}
