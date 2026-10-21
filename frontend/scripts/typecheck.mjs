import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const core = process.env.OPEN_AGENT_WORLD_CORE_ONLY === '1' || process.env.OAW_ALLOW_PACK_FAILURES === '1';
const result = spawnSync(process.execPath, [require.resolve('typescript/bin/tsc'), '--noEmit', '-p', core ? 'tsconfig.core.json' : 'tsconfig.app.json'],
  { stdio: 'inherit', windowsHide: true });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
