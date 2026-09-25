import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { realpathSync, rmSync } from 'node:fs';

const root = fileURLToPath(new URL('../', import.meta.url));
const sdkRoot = realpathSync(path.join(root, 'pack-sdk'));
if (path.dirname(sdkRoot) !== realpathSync(root)) throw new Error('SDK output must stay inside frontend');
const typesDirectory = path.join(sdkRoot, 'types');
// This directory contains only generated declarations; discard stale outputs.
rmSync(typesDirectory, { recursive: true, force: true });
const config = ts.readConfigFile(path.join(root, 'tsconfig.app.json'), ts.sys.readFile);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
const program = ts.createProgram([path.join(root, 'src/plugins/sdk.ts'), path.join(root, 'src/vite-env.d.ts')], {
  ...parsed.options, noEmit: false, declaration: true, emitDeclarationOnly: true,
  rootDir: path.join(root, 'src'), outDir: typesDirectory,
});
const result = program.emit();
const errors = [...ts.getPreEmitDiagnostics(program), ...result.diagnostics].filter(d => d.category === ts.DiagnosticCategory.Error);
if (errors.length) {
  process.stderr.write(ts.formatDiagnosticsWithColorAndContext(errors, {
    getCanonicalFileName: f => f, getCurrentDirectory: () => root, getNewLine: () => '\n',
  }));
  process.exitCode = 1;
}
