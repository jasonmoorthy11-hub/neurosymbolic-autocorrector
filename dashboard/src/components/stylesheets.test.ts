// Cascade guard for the split stylesheets. Every component .css file is imported
// by its component, and App.tsx imports the components before ./App.css, so the
// bundle puts every component rule BEFORE every App.css rule. A component rule
// that ties an App.css rule on specificity and sets a related property on the
// same element therefore loses, whichever came first in the old single file.
// This test forbids such ties for element-qualified selectors (".x h3" vs
// ".panel h3"), the shape that slipped through the split's mechanical check.
/// <reference types="node" />
import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";

const srcDir = new URL("../", import.meta.url);
const componentsDir = new URL("./", import.meta.url);

interface Rule {
  file: string;
  selector: string;
  props: string[];
}

function parseRules(css: string, file: string): Rule[] {
  const out: Rule[] = [];
  const src = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const re = /([^{}]+)\{|\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (m[0] === "}") continue;
    const head = m[1].trim();
    if (head.startsWith("@")) continue; // media/supports block: its inner rules follow
    const end = src.indexOf("}", re.lastIndex);
    const props = src
      .slice(re.lastIndex, end)
      .split(";")
      .map((d) => d.split(":")[0].trim())
      .filter(Boolean);
    for (const selector of head.split(",").map((s) => s.trim())) out.push({ file, selector, props });
    re.lastIndex = end + 1;
  }
  return out;
}

/** (classes, elements) of a selector; ids are not used in this codebase. */
function specificity(selector: string): string {
  const s = selector.replace(/::?[a-z-]+(\([^)]*\))?/g, (x) => (x.startsWith("::") ? " e" : " c"));
  const classes = (s.match(/\.[\w-]+/g) ?? []).length + (s.match(/\[[^\]]+\]/g) ?? []).length + (s.match(/\bc\b/g) ?? []).length;
  const elements = (s.replace(/[.#][\w-]+|\[[^\]]+\]/g, " ").match(/\b[a-z][a-z0-9]*\b/g) ?? []).filter((w) => w !== "c").length;
  return `${classes},${elements}`;
}

/** The bare element a selector ends in (".panel h3" -> "h3"), or null when it ends in a class. */
function bareTag(selector: string): string | null {
  const last = selector.trim().split(/\s+/).pop() ?? "";
  return /^[a-z][a-z0-9]*$/.test(last) ? last : null;
}

const related = (p: string, q: string) => p === q || p.startsWith(`${q}-`) || q.startsWith(`${p}-`);

const app = parseRules(readFileSync(new URL("App.css", srcDir), "utf8"), "App.css");
const components = readdirSync(componentsDir)
  .filter((f) => f.endsWith(".css"))
  .flatMap((f) => parseRules(readFileSync(new URL(f, componentsDir), "utf8"), f));

describe("component stylesheets never tie App.css on an element-qualified selector", () => {
  it("parses both sides", () => {
    expect(app.length).toBeGreaterThan(20);
    expect(components.length).toBeGreaterThan(20);
  });

  it("has no same-tag, same-specificity, related-property pair", () => {
    const ties: string[] = [];
    for (const a of app) {
      const tag = bareTag(a.selector);
      if (!tag) continue;
      for (const b of components) {
        if (bareTag(b.selector) !== tag) continue;
        if (specificity(a.selector) !== specificity(b.selector)) continue;
        const overlap = a.props.filter((p) => b.props.some((q) => related(p, q)));
        if (overlap.length) ties.push(`${b.file} "${b.selector}" ties App.css "${a.selector}" on ${overlap.join(", ")}`);
      }
    }
    expect(ties, "raise the component selector's specificity (e.g. prefix its container class)").toEqual([]);
  });
});
