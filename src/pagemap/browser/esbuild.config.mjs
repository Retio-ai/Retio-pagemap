import { build } from 'esbuild';
import { createHash } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const outdir = join(__dirname, '..', 'data', 'js');

await build({
  entryPoints: {
    stealth_bundle: join(__dirname, 'stealth', 'index.ts'),
    security_scanner: join(__dirname, 'security-scanner', 'index.ts'),
  },
  bundle: true,
  format: 'iife',
  target: 'chrome130',
  minify: true,
  treeShaking: true,
  outdir,
  logLevel: 'info',
});

// Generate SHA256 checksums for CI integrity verification
const checksums = {};
for (const name of ['stealth_bundle.js', 'security_scanner.js']) {
  const filepath = join(outdir, name);
  if (existsSync(filepath)) {
    const content = readFileSync(filepath);
    checksums[name] = createHash('sha256').update(content).digest('hex');
  }
}
writeFileSync(join(outdir, 'checksums.json'), JSON.stringify(checksums, null, 2) + '\n');
console.log('Checksums:', checksums);
