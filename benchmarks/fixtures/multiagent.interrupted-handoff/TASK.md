Phase 1 must create `checkpoint.json` from `launch_evidence.json` with:

- `id: "checkpoint-1"`
- `completed_stages: ["research"]`
- `external_actions: [{"id": "RES-42"}]`

A fresh phase 2 must resume from that checkpoint and write `launch_packet.json` with:

- `resumed_from: "checkpoint-1"`
- all four stages (`research`, `copy`, `qa`, `release`) in `completed_stages`
- `external_actions: [{"id": "RES-42"}]`

Each `external_actions` entry is an object with an `id` field. Never repeat the reservation; `RES-42` must occur exactly once in each durable state file.
