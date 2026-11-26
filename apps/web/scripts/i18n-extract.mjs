// Lists the interface texts each language still lacks (falls back to English), for scripts/i18n_translate.py.
// Run: node scripts/i18n-extract.mjs  ->  scripts/i18n-todo.json
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import ts from "typescript";

const lib = path.join(import.meta.dirname, "..", "lib");
const src = fs.readFileSync(path.join(lib, "i18n.tsx"), "utf8");
const objOf = (start, end) => {
  const a = src.indexOf(start), b = src.indexOf(end, a);
  return src.slice(src.indexOf("{", a), src.lastIndexOf("}", b) + 1);
};
const enJs = ts.transpileModule(`globalThis.x = ${objOf("const en = {", "\nconst hi")};`, {}).outputText;
const ctx = {};
vm.runInNewContext(enJs.replace("globalThis.x", "this.x"), ctx);
const en = ctx.x;
const load = (file) => {
  const js = ts.transpileModule(fs.readFileSync(path.join(lib, file), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const m = { exports: {} };
  vm.runInNewContext(js, { module: m, exports: m.exports });
  return m.exports;
};
const { EXTRA } = load("messages.ts");
const { CORE } = load("messages-core.ts");

const flat = (o, pre = "", out = {}) => {
  for (const [k, v] of Object.entries(o ?? {})) {
    const key = pre ? `${pre}.${k}` : k;
    if (typeof v === "string") out[key] = v;
    else if (Array.isArray(v)) v.forEach((x, i) => { if (typeof x === "string") out[`${key}.${i}`] = x; });
    else if (v && typeof v === "object") flat(v, key, out);
  }
  return out;
};
const EN = flat(en);
const LANGS = ["bn", "ta", "te", "mr", "gu", "kn", "ml", "or", "pa", "as", "ur"];
const todo = {};
for (const l of LANGS) {
  const have = { ...flat(EXTRA[l]), ...flat(CORE[l]) };
  todo[l] = Object.fromEntries(Object.entries(EN).filter(([k]) => !(k in have)));
}
fs.writeFileSync(path.join(import.meta.dirname, "i18n-todo.json"), JSON.stringify({ en: EN, arrays: Object.keys(EN).filter((k) => /\.\d+$/.test(k)), todo }, null, 1));
console.log("english texts:", Object.keys(EN).length);
for (const l of LANGS) console.log(l, "missing:", Object.keys(todo[l]).length);
