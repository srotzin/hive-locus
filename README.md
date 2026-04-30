# HiveLocus — Active Coordinate Engine

A Trident-of-Tridents. 9 inference heads across 3 axes. One output: a spatial coordinate.

```
Trident-X  →  Trust     (0.0 untrusted → 1.0 fully trusted)
Trident-Y  →  Velocity  (0.0 stalled   → 1.0 high momentum)
Trident-Z  →  Depth     (0.0 surface   → 1.0 deep/FENR, shell 1–6)
                ↓
         Meta-synthesis
                ↓
         (X, Y, Z) coordinate → HiveDimensions → HivePhysics
```

## What it does

HiveDimensions derives agent coordinates passively from pulse.smsh data using deterministic formulas. HiveLocus makes positioning **active** — 9 heads reason about what an entity's coordinate should be, using context that formula-based derivation cannot see.

Any entity can be located:
- **Agents** — new agent arrives, Locus places it at an earned starting position
- **IoT sensors** — structural reading → coordinate in damage space (feeds HIVESHM)
- **Markets** — order flow → position in trust/velocity/depth market space
- **Custom** — any context can be located

## Endpoints

| Endpoint | What it does | Price |
|---|---|---|
| `POST /locus/locate` | General coordinate engine | $0.03 x402 |
| `POST /locus/locate/agent` | Locate agent by DID, push to HiveDimensions | $0.03 |
| `POST /locus/locate/sensor` | IoT sensor reading → damage coordinate | $0.03 |
| `POST /locus/locate/market` | Market instrument → market coordinate | $0.03 |
| `GET /locus/status` | Service state, DID, tier | free |
| `GET /llms.txt` | Discovery | free |
| `GET /.well-known/agent.json` | A2A agent card | free |

## Why Trident-of-Tridents and not Phalanx

Phalanx chains **depth** — same task refined through 3 passes.
Trident-of-Tridents chains **breadth** — each Trident owns one dimension.

The output of a Phalanx is a better answer.
The output of HiveLocus is a position in space.

These are fundamentally different operations.

## Downstream

Coordinates emitted to:
- `https://hivedimensions.onrender.com` — persists trajectory, starmap, center of mass
- `https://hivephysics.onrender.com` — applies mass/momentum/entropy/coherence laws

## Network

- pulse.smsh: `https://hive-pulse.onrender.com/pulse/tiers`
- Integration guide: `https://github.com/srotzin/hive-pulse/blob/master/INTEGRATE.md`
- Entry point: `https://hivegate.onrender.com/v1/gate/onboard`


---

## Hive Civilization

Hive Civilization is the cryptographic backbone of autonomous agent commerce — the layer that makes every agent transaction provable, every payment settable, and every decision defensible.

This repository is part of the **PROVABLE · SETTABLE · DEFENSIBLE** pillar.

- thehiveryiq.com
- hiveagentiq.com
- agent-card: https://hivetrust.onrender.com/.well-known/agent-card.json
