# ssm

Six `Command` documents (`jarvis-deploy`, `jarvis-restart`, `jarvis-secrets-sync`,
`jarvis-status`, and PLAN AD40's `jarvis-ofw-login`, `jarvis-ofw-reset`), schemaVersion 2.2, one `aws:runShellScript` step, run as root. Bodies come
from `ops/aws/ssm/<name>.sh` through the `documents` map (AD19). The module prepends
`#!/usr/bin/env bash` and the parameter exports, so a body reads:

| Document | Parameters | Environment in the body |
|---|---|---|
| `jarvis-deploy` | `Sha` (`^[0-9a-f]{40}$`) | `SHA` |
| `jarvis-restart` | `Mode` (`work`/`personal`), `Rotate` (`false`/`true`, default `false`) | `MODE`, `ROTATE` |
| `jarvis-secrets-sync` | none | none |
| `jarvis-status` | none | none |
| `jarvis-ofw-login` | `WaitSeconds` (`^[0-9]{1,4}$`, default `900`) | `WAIT_SECONDS` |
| `jarvis-ofw-reset` | none | none |

Bodies must revalidate their inputs. A body's own leading shebang is dropped.

Also `SSM-SessionManagerRunShell` session preferences: no S3 or CloudWatch session logging
(rule 0.4), `idleSessionTimeout = 20`, `runAsEnabled = false` (sessions run as `ssm-user`).
