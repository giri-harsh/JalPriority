import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCREENS_DIR = resolve(HERE, "../screens");
const ALLOWED_RUNTIME_PACKAGES = new Set(["react", "react-dom", "leaflet", "react-leaflet"]);

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}

const files = walk(SCREENS_DIR);
const sources = files.filter((file) => /\.(ts|tsx)$/.test(file));
const styleSheets = files.filter((file) => file.endsWith(".css"));
const read = (file: string) => readFileSync(file, "utf-8");
const rel = (file: string) => relative(SCREENS_DIR, file).replace(/\\/g, "/");

function offenders(list: string[], pattern: RegExp): string[] {
  return list.filter((file) => pattern.test(read(file))).map(rel);
}

/** Selector lists of every style rule, skipping @keyframes bodies. */
function selectorLists(css: string): string[] {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const selectors: string[] = [];
  let depth = 0;
  let buffer = "";
  const skipDepths: number[] = [];
  for (const char of stripped) {
    if (char === "{") {
      const prelude = buffer.trim();
      const insideKeyframes = skipDepths.length > 0;
      if (prelude.startsWith("@")) {
        if (/^@(-\w+-)?keyframes/.test(prelude)) skipDepths.push(depth);
      } else if (!insideKeyframes) {
        selectors.push(prelude);
      }
      depth += 1;
      buffer = "";
    } else if (char === "}") {
      depth -= 1;
      if (skipDepths.at(-1) === depth) skipDepths.pop();
      buffer = "";
    } else if (char === ";") {
      buffer = "";
    } else {
      buffer += char;
    }
  }
  return selectors;
}

describe("isolation of src/screens", () => {
  it("scans a meaningful set of files", () => {
    expect(sources.length).toBeGreaterThan(15);
    expect(styleSheets.length).toBeGreaterThan(8);
  });

  it('never mentions the B API ("/b/")', () => {
    expect(offenders([...sources, ...styleSheets], /\/b\//)).toEqual([]);
  });

  it("never reads import.meta.env", () => {
    expect(offenders(sources, /import\.meta\.env/)).toEqual([]);
  });

  it("does not use a router", () => {
    expect(offenders(sources, /react-router/)).toEqual([]);
  });

  it("does not touch browser storage", () => {
    expect(offenders(sources, /localStorage|sessionStorage|indexedDB/)).toEqual([]);
  });

  it("never imports from the harness", () => {
    expect(offenders(sources, /from\s+["'][^"']*harness/)).toEqual([]);
    expect(offenders(sources, /import\(["'][^"']*harness/)).toEqual([]);
  });

  it("imports only react, react-dom, leaflet and react-leaflet as runtime packages", () => {
    const bad: string[] = [];
    for (const file of sources) {
      for (const match of read(file).matchAll(/(?:from|import)\s+["']([^"']+)["']/g)) {
        const specifier = match[1] as string;
        if (specifier.startsWith(".")) continue;
        const pkg = specifier.startsWith("@") ? specifier.split("/").slice(0, 2).join("/") : (specifier.split("/")[0] as string);
        if (!ALLOWED_RUNTIME_PACKAGES.has(pkg)) bad.push(`${rel(file)} -> ${specifier}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("only imports global CSS from leaflet, and only once", () => {
    const cssImports = sources.flatMap((file) =>
      Array.from(read(file).matchAll(/import\s+["']([^"']+\.css)["']/g)).map((match) => `${rel(file)}: ${match[1]}`),
    );
    expect(cssImports).toEqual(["components/RouteMap.tsx: leaflet/dist/leaflet.css"]);
  });

  it("makes network calls only through the api client, with base-relative URLs", () => {
    const fetchers = sources.filter((file) => /\bfetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon/.test(read(file))).map(rel);
    expect(fetchers).toEqual(["api.ts"]);
    expect(read(join(SCREENS_DIR, "api.ts"))).toContain("fetch(`${this.base}${path}`");
    expect(offenders(sources, /["'`]https?:\/\/(?!tile\.openstreetmap\.org)/)).toEqual([]);
  });

  it("keeps hard-coded colours out of the sources", () => {
    expect(offenders([...sources, ...styleSheets], /#[0-9a-fA-F]{3,8}\b(?![\w-])/)).toEqual([]);
    expect(offenders(styleSheets, /\b(?:rgba?|hsla?|hwb|lab|lch)\(/)).toEqual([]);
  });
});

describe("CSS modules", () => {
  it.each(styleSheets.map((file) => [rel(file), file] as const))("%s uses only class-scoped selectors", (_name, file) => {
    const css = read(file);
    for (const selector of selectorLists(css)) {
      for (const part of selector.split(",")) {
        expect(part, `selector "${part.trim()}" must contain a class`).toMatch(/\./);
      }
    }
  });

  it.each(styleSheets.map((file) => [rel(file), file] as const))("%s has no global hooks or fixed positioning", (_name, file) => {
    const css = read(file);
    expect(css).not.toMatch(/(^|[\s,}>+~])(?:body|html)\b(?!-)/m);
    expect(css).not.toMatch(/:root/);
    expect(css).not.toMatch(/:global/);
    expect(css).not.toMatch(/position\s*:\s*fixed/);
  });

  it.each(styleSheets.map((file) => [rel(file), file] as const))("%s sets fonts only through the design tokens", (_name, file) => {
    for (const match of read(file).matchAll(/font-family\s*:\s*([^;]+);/g)) {
      expect(match[1]).toMatch(/var\(--jp-(font|mono)\)/);
    }
  });

  it("selectorLists understands nested at-rules (self-check of the lint)", () => {
    const css = "@media (max-width: 1px) { .a { color: red } } @keyframes k { from { top: 0 } to { top: 1px } } .b .c, .d { x: y }";
    expect(selectorLists(css)).toEqual([".a", ".b .c, .d"]);
  });
});
