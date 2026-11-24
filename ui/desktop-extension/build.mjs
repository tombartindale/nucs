// Builds dist/nucs.mcpb: the extension bundled into one file with esbuild (so the .mcpb
// carries no node_modules), next to its manifest, then packed and validated by the
// official mcpb CLI. The server serves the result at /api/mcp/extension.
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const stage = join(here, 'build');
const dist = join(here, 'dist');
rmSync(stage, { recursive: true, force: true });
mkdirSync(join(stage, 'server'), { recursive: true });
mkdirSync(dist, { recursive: true });

await build({
  entryPoints: [join(here, 'src', 'index.ts')],
  outfile: join(stage, 'server', 'index.mjs'),
  bundle: true, platform: 'node', format: 'esm', target: 'node18',
  // Some bundled CommonJS dependencies call require(); give the ESM bundle one.
  banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
  logLevel: 'warning',
});

const pkg = JSON.parse(readFileSync(join(here, 'package.json'), 'utf8'));
const manifest = JSON.parse(readFileSync(join(here, 'manifest.json'), 'utf8'));
manifest.version = pkg.version;
writeFileSync(join(stage, 'manifest.json'), JSON.stringify(manifest, null, 2));

const cli = join(here, 'node_modules', '.bin', 'mcpb');
const bin = [cli, join(here, '..', 'node_modules', '.bin', 'mcpb')].find((p) => { try { readFileSync(p); return true; } catch { return false; } });
execFileSync(bin, ['validate', join(stage, 'manifest.json')], { stdio: 'inherit' });
execFileSync(bin, ['pack', stage, join(dist, 'nucs.mcpb')], { stdio: 'inherit' });
