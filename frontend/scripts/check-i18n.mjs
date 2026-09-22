#!/usr/bin/env node
/**
 * i18n guard.
 *
 * Two checks that TypeScript cannot make on its own:
 *
 * 1. **No user-facing string lives outside the dictionary.** `tsc` proves every
 *    dictionary key is translated, but it cannot see a literal that was never
 *    moved into the dictionary in the first place. This walks the source and
 *    fails on any CJK character in a file other than `dictionaries.ts`.
 *
 * 2. **The locales have identical key sets.** `en` is typed
 *    `Record<MessageKey, string>`, so a *missing* key is already a compile
 *    error; this also catches a *duplicate* or an extra key that the type would
 *    tolerate by coincidence.
 *
 * Run with `npm run check:i18n`. Exits non-zero on failure so it can gate CI.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const SRC = join(ROOT, "src");
const DICTIONARY = join("src", "lib", "i18n", "dictionaries.ts");

/**
 * CJK ideographs, plus CJK punctuation (。、「」…).
 *
 * Deliberately **not** the full-width forms block (U+FF00–U+FFEF): that range is
 * mostly punctuation and full-width Latin, and a lone separator such as "；" is
 * not untranslated *content* — flagging it would push a caller into a contrived
 * workaround for something no reader would notice. A stray ideograph is the
 * signal worth failing the build over.
 */
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f]/;

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

const problems = [];

// --- 1. no stray literals ---------------------------------------------------
for (const file of walk(SRC)) {
  const rel = relative(ROOT, file);
  if (rel.split(sep).join("/") === DICTIONARY.split(sep).join("/")) continue;

  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, index) => {
    if (CJK.test(line)) {
      problems.push(
        `${rel}:${index + 1}  hard-coded CJK outside the dictionary\n    ${line.trim()}`,
      );
    }
  });
}

// --- 2. key parity ---------------------------------------------------------
const source = readFileSync(join(ROOT, DICTIONARY), "utf8");

function keysOf(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  if (start === -1 || end === -1) {
    throw new Error(`could not locate the ${startMarker} block in dictionaries.ts`);
  }
  return source
    .slice(start, end)
    .split("\n")
    .map((line) => line.match(/^\s{2}"([^"]+)":/))
    .filter(Boolean)
    .map((m) => m[1]);
}

const zh = keysOf("const zhHK = {", "} as const;");
const en = keysOf("const en: Record<MessageKey, string> = {", "\n};");

const duplicates = zh.filter((key, i) => zh.indexOf(key) !== i);
if (duplicates.length) problems.push(`duplicate keys in zh-HK: ${duplicates.join(", ")}`);

const missingInEn = zh.filter((key) => !en.includes(key));
if (missingInEn.length) problems.push(`missing from en: ${missingInEn.join(", ")}`);

const extraInEn = en.filter((key) => !zh.includes(key));
if (extraInEn.length) problems.push(`not present in zh-HK: ${extraInEn.join(", ")}`);

// --- 3. no key is defined but never used ------------------------------------
//
// A key that nothing references is dead weight with a maintenance cost: it gets
// translated, it drifts out of sync with the UI it no longer describes, and it
// looks live to the next reader. Nine such keys accumulated during the initial
// extraction (all speculative `common.*` entries), so this is checked rather
// than trusted.
//
// The two locale tables are *definitions*, not usages, so they are stripped
// first. What remains of the dictionary file — `LABEL_KEYS` and the helpers — is
// a genuine reference site, as is every other source file.
const sourceFiles = walk(SRC).filter(
  (file) => relative(ROOT, file).split(sep).join("/") !== DICTIONARY.split(sep).join("/"),
);

const referenceCorpus = [
  ...sourceFiles.map((file) => readFileSync(file, "utf8")),
  source
    .replace(/const zhHK = \{[\s\S]*?\n\} as const;/, "")
    .replace(/const en: Record<MessageKey, string> = \{[\s\S]*?\n\};/, ""),
].join("\n");

const unused = zh.filter((key) => !referenceCorpus.includes(`"${key}"`));
if (unused.length) {
  problems.push(
    `defined but never referenced (delete them, or wire them up):\n    ${unused.join("\n    ")}`,
  );
}

// --- report ----------------------------------------------------------------
if (problems.length) {
  console.error("i18n check FAILED\n");
  for (const problem of problems) console.error(`  ${problem}\n`);
  process.exit(1);
}

console.log(
  `i18n check passed — ${zh.length} keys; zh-HK and en in sync; no stray literals; no unused keys.`,
);
