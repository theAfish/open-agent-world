import type { FullResult, Reporter } from "@playwright/test/reporter";
import { writeFileSync } from "node:fs";

export default class CompletionReporter implements Reporter {
  onEnd(result: FullResult) {
    const output = process.env.OAW_E2E_RESULT_FILE;
    if (!output) return;
    writeFileSync(output, JSON.stringify({ status: result.status }), "utf8");
  }
}
