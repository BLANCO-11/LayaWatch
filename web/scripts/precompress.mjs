/* Build-time precompression for the static export (catalog docs/performance.md 4.6, item 31).
 * Runs as `postbuild` after `next build`: for every compressible asset in out/ at least
 * MIN_BYTES, writes a `.br` (brotli) and/or `.gz` (gzip level 9) sidecar whenever the
 * sidecar actually shrinks the file. layawatch/http/static.py serves the sidecars
 * directly on Accept-Encoding, so the server never compresses at runtime.
 * Extensions stay in sync with `_PRECOMPRESS_TYPES` in layawatch/http/static.py.
 * Fonts and images are already compressed; html/css/js/json/svg/txt are not. */
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const OUT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "out");
/** Sidecars below this size are not worth the extra file or lookup. */
const MIN_BYTES = 1024;
const EXTS = new Set([".html", ".css", ".js", ".mjs", ".json", ".map", ".svg", ".txt"]);

let scanned = 0;
let brWritten = 0;
let gzWritten = 0;
let sidecarBytes = 0;
let identityBytes = 0;

function walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(path);
      continue;
    }
    if (!entry.isFile()) continue;
    const ext = entry.name.slice(entry.name.lastIndexOf("."));
    if (!EXTS.has(ext) || entry.name.endsWith(".br") || entry.name.endsWith(".gz")) continue;
    const source = readFileSync(path);
    if (source.length < MIN_BYTES) continue;
    scanned += 1;
    identityBytes += source.length;
    const br = brotliCompressSync(source, {
      params: {
        [constants.BROTLI_PARAM_QUALITY]: 11,
        [constants.BROTLI_PARAM_SIZE_HINT]: source.length,
      },
    });
    if (br.length < source.length) {
      writeFileSync(path + ".br", br);
      brWritten += 1;
      sidecarBytes += br.length;
    }
    const gz = gzipSync(source, { level: 9 });
    if (gz.length < source.length) {
      writeFileSync(path + ".gz", gz);
      gzWritten += 1;
      sidecarBytes += gz.length;
    }
  }
}

try {
  walk(OUT);
} catch (error) {
  console.error(`precompress: ${error.message}`);
  process.exit(1);
}
if (scanned === 0) {
  console.log("precompress: nothing to do (no web/out export?)");
} else {
  console.log(
    `precompress: ${scanned} files (${identityBytes} B) -> ` +
      `${brWritten} .br + ${gzWritten} .gz sidecars, ${sidecarBytes} B on disk`,
  );
}
