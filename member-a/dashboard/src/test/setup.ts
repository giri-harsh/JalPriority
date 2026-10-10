import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom reports a 0x0 layout box, which makes Leaflet compute invalid bounds.
for (const [prop, value] of [
  ["clientWidth", 800],
  ["clientHeight", 400],
  ["offsetWidth", 800],
  ["offsetHeight", 400],
] as const) {
  Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value });
}

// Leaflet only picks its SVG renderer when the browser can create SVG rects; jsdom cannot.
if (!("createSVGRect" in SVGSVGElement.prototype)) {
  Object.defineProperty(SVGSVGElement.prototype, "createSVGRect", { configurable: true, value: () => ({}) });
}

afterEach(() => {
  cleanup();
});
