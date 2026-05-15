# Scripts

Operator-facing scripts. Phase 1 contents:

| Script | Purpose | When you run it |
|--------|---------|-----------------|
| `keychain_setup.sh` | Interactive credential storage | Once per phase that adds credentials |
| `keychain_verify.sh` | Confirm credentials present | After setup; whenever auth breaks |
| `smoke_test.py` | End-to-end connectivity check | Once per phase as part of acceptance |
| `snapshot_backup.sh` | Manual brain backup | End of each phase as a checkpoint |

All scripts run from the `agent` account in `~/agents/`.
