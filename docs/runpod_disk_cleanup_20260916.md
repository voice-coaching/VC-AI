# RunPod disk cleanup observation

Observed 2026-09-16 KST (2026-09-15 15:38–15:42 UTC), target `urwnuesv7o4fv6`, SSH `194.68.245.215:22152`. Source A40 `s8hb5b0l5k4qlx` remained EXITED.

B2 Native API listed existing objects in bucket `speakai2026`. Two inactive recovery archives under `/workspace/intelligentai/deployment/service-supervision-20260910-6618a39/` matched B2 object sizes and SHA-1 and were removed from RunPod:

| File | Bytes | SHA-1 |
|---|---:|---|
| current-overlay.tar.gz | 76,751 | dbf67211d92ad183fba4f675d505e7a659bff49e |
| previous-overlay.tar.gz | 50,891 | ef9490effeff82e63f59683cdb2df59c938ae6b2 |

Recovery prefix: `intelligentai/recovery/service-supervision-20260910-6618a39/`. B2 objects were retained. Total removed file payload: 127,642 bytes; physical reclaimed blocks were not measured. No new B2 uploads.

The capacity blocker remains: container root disk is 5 GB, 96% used, 227 MB available after cleanup. `/opt/intelligentai/envs` occupies about 4.4 GiB with partially restored environments. The separate `/workspace` has approximately 80 GiB under intelligentai: 63 GiB releases, 17 GiB Python environments, 1.3 GiB runtime assets (rounded du values). Shared filesystem df is not the individual Pod volume quota.

Large external datasets and download caches were not found in this tree. Model weights, source releases, active overlay, Python environments, runtime assets and credentials were preserved. Removing workspace backups does not free container root disk. Successful runtime restoration requires adequate container capacity or a separately designed runtime storage change; cleanup has not resolved startup.

Validation: API settings, SSH disk usage, B2 metadata/hash comparison, and deleted-file absence. No inference QA, Pod restart, disk resize, deployment, commit, push or PR. Existing user changes, including deleted local guidance files, were preserved.
