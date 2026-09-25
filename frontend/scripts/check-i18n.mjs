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
//
// The dictionaries were split into `src/lib/i18n/namespaces/` (D5). Those files
// are *where the translations live*, so they are the one legitimate home for
// CJK — the same exemption the monolithic `dictionaries.ts` used to have.
for (const file of walk(SRC)) {
  const rel = relative(ROOT, file).split(sep).join("/");
  if (rel === DICTIONARY.split(sep).join("/")) continue;
  if (rel.startsWith("src/lib/i18n/namespaces/")) continue;

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
//
// The dictionary was split per feature (D5), so the keys now live in
// `src/lib/i18n/namespaces/*.ts` and `dictionaries.ts` only composes them.
// This walks the namespace files directly.
//
// Reading the *files* rather than importing them matters: the point of this
// check is to validate the source, and importing would evaluate the module
// (needing a TS loader, and a compile error would surface as an import crash
// rather than a readable problem).
const NAMESPACES_DIR = join(ROOT, "src", "lib", "i18n", "namespaces");

const namespaceFiles = readdirSync(NAMESPACES_DIR)
  .filter((f) => f.endsWith(".ts"))
  .sort();

if (!namespaceFiles.length) {
  throw new Error(`no namespace files found in ${NAMESPACES_DIR}`);
}

/** Pull the `"key": "value",` pairs from one exported object literal. */
function keysOf(text, startMarker) {
  const start = text.indexOf(startMarker);
  if (start === -1) {
    throw new Error(`could not locate the ${startMarker} block`);
  }
  // Walk braces so a value containing `}` cannot end the block early.
  let i = text.indexOf("{", start) + 1;
  let depth = 1;
  const chunk = [];
  while (depth > 0 && i < text.length) {
    if (text[i] === "{") depth += 1;
    if (text[i] === "}") depth -= 1;
    if (depth > 0) chunk.push(text[i]);
    i += 1;
  }
  return chunk
    .join("")
    .split("\n")
    .map((line) => line.match(/^\s*"([^"]+)":/))
    .filter(Boolean)
    .map((m) => m[1]);
}

const zh = [];
const en = [];
for (const file of namespaceFiles) {
  const text = readFileSync(join(NAMESPACES_DIR, file), "utf8");
  zh.push(...keysOf(text, "export const zhHK = {"));
  en.push(...keysOf(text, "export const en = {"));
}

// The composer must reference every namespace file. A file on disk that is
// never merged is a silently dead translation set — the keys exist, `tsc` is
// happy, and the UI renders the raw key string.
{
  const composer = readFileSync(join(ROOT, DICTIONARY), "utf8");
  const unwired = namespaceFiles
    .map((f) => f.replace(/\.ts$/, ""))
    .filter((name) => !new RegExp(`from "\\./namespaces/${name}"`).test(composer));
  if (unwired.length) {
    problems.push(
      `namespace files exist but are not imported by dictionaries.ts: ${unwired.join(", ")}`,
    );
  }
}

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
// The namespace files are *definitions*, not usages, so they are excluded —
// including them would let every key reference itself and no key could ever be
// reported unused.
//
// `dictionaries.ts` is *included*: after the D5 split it no longer holds the
// strings, and it is a genuine reference site for every key that appears in
// `LABEL_KEYS` or `SERVER_CODES`. Its spread-merge lines name the namespaces
// rather than the keys, so they contribute nothing either way.
const sourceFiles = walk(SRC).filter((file) => {
  const rel = relative(ROOT, file).split(sep).join("/");
  return !rel.startsWith("src/lib/i18n/namespaces/");
});

const referenceCorpus = sourceFiles
  .map((file) => readFileSync(file, "utf8"))
  .join("\n");

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
