---
name: execution-configuration
description: Inspect ordinary invocation configuration locally without contacting a service.
---

Run `scripts/describe.py` with an installed Python interpreter through
`run_skill_script`. Select an authorized Sandbox, and optionally one Environment
Profile and one Compute Target. Set the ordinary variable `DEMO_REGION` to a
non-sensitive label such as `local-test`.

The script reads `DEMO_REGION` and the documented `OAW_TARGET_CONFIG_JSON` carrier.
It prints the ordinary region, descriptive provider ID, and the number of target
configuration fields. It does not enumerate the process environment, print
credentials, contact a service, or install anything.
