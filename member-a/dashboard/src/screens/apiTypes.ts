// Response shapes of A's own API (CONTRACT 5.3 to 5.5) that the screens read.
// The shared props contract lives in ../contract/types.ts and is not repeated here.

export interface ScenarioSummary {
  schema_version: "1.0";
  scenario_id: string;
  parent_scenario_id: string | null;
  service_area_id: string;
  window_index: number;
  label: string;
  n_areas: number;
  supply_litres: number;
  tankers_available: number;
  alert_level: number;
  is_synthetic: boolean;
}

export interface ScenarioTanker {
  tanker_id: string;
  capacity_litres: number;
  status: "available" | "unavailable";
  max_route_km: number;
}

export interface HistoryWindow {
  window_index: number;
  need_litres: number;
  received_litres: number;
  max_temp_c: number;
  alert_level: number;
}

export interface ScenarioArea {
  area_id: string;
  name: string;
  lat: number;
  lon: number;
  population: number;
  vulnerability_index: number;
  vulnerability_available: boolean;
  heat_exposure: number;
  expected_piped_litres: number;
  requested_litres: number;
  request_time: string;
  last_delivery_litres: number;
  last_delivery_at: string | null;
  history: HistoryWindow[];
}

export interface Scenario {
  schema_version: "1.0";
  scenario_id: string;
  parent_scenario_id: string | null;
  service_area_id: string;
  service_area_name: string;
  window_index: number;
  window_start: string;
  window_hours: number;
  is_synthetic: boolean;
  seed: number;
  heat: { alert_level: number; max_temp_c: number; heat_index_c: number };
  supply: { tanker_water_available_litres: number };
  depot: { depot_id: string; name: string; lat: number; lon: number };
  tankers: ScenarioTanker[];
  areas: ScenarioArea[];
  distance_km: Record<string, Record<string, number>>;
  disruptions_applied: { type: string; params: Record<string, unknown>; applied_at: string }[];
}

export interface TopFeature {
  feature: string;
  contribution_litres: number;
}

export interface AreaPrediction {
  area_id: string;
  predicted_need_litres: number;
  expected_piped_litres: number;
  predicted_shortfall_litres: number;
  baseline_last_need_litres: number;
  top_features: TopFeature[];
}

export interface PredictionSet {
  schema_version: "1.0";
  scenario_id: string;
  model_version: string;
  generated_at: string;
  latency_ms: number;
  run_mae_litres: number | null;
  is_simulation: boolean;
  areas: AreaPrediction[];
}

export interface ErrorMetrics {
  mae_litres: number;
  rmse_litres: number;
}

export interface ModelInfo {
  schema_version: "1.0";
  model_version: string;
  trained_at: string;
  train_rows: number;
  test_rows: number;
  target: string;
  metrics: { xgboost: ErrorMetrics; last_observed_baseline: ErrorMetrics };
  feature_importances: { feature: string; importance: number }[];
  is_simulation: boolean;
  disclaimer: string;
}
