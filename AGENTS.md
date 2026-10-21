
# OAW Engineering & Testing Guidelines

## Implementation
- Prioritize the simplest solution that meets the actual requirement.
- Reuse existing mechanisms; do not introduce abstractions or compatibility layers without a demonstrated need.
- Remove obsolete mechanisms when safe instead of continuously adding patches.
- Keep changes within the requested scope.

## Testing
- Select tests based on the changed behavior and its actual risk.
- During development, run the smallest relevant test files first.
- Do not repeatedly run the complete pytest, Vitest, or E2E suites after every change.
- Use targeted regression tests for bugs. Each new test must cover a distinct, realistic failure mode not already covered.
- Avoid duplicate assertions across unit, integration, and E2E layers unless they validate meaningfully different boundaries.
- Do not build new test infrastructure or large mock frameworks for a small feature.
- Prefer reusing existing fixtures and test utilities.
- After implementation, run relevant subsystem tests. Leave the complete cross-project regression to the required PR CI unless broader local validation is specifically justified.
- Never manually trigger duplicate full CI runs for an unchanged commit.
- If repeated fixes or tests fail to converge, re-examine the design assumptions rather than adding more tests and patches.
- Do not delete tests, weaken assertions, or add skips just to obtain a passing result.
- Security, authorization, persistence, migrations, and plugin lifecycle changes require appropriate negative and integration testing.

## Reporting
- Report which tests actually ran, their results, and important coverage gaps.
- Do not claim full validation based only on targeted tests.
- If validation becomes disproportionately expensive, explain the remaining risk instead of expanding the test scope automatically.
