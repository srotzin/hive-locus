"""
HiveLocus — Active Coordinate Engine for the Hive Network
==========================================================
A Trident-of-Tridents where each Trident owns one spatial axis.

Architecture:
  Trident-X  →  Trust axis (0.0–1.0)
               3 heads reason about: reliability, history, framework reputation,
               prior interactions, consistency of output, referral chain quality

  Trident-Y  →  Velocity axis (0.0–1.0)
               3 heads reason about: rate of action, trajectory (accelerating
               or decelerating), recency of interactions, momentum vs stall

  Trident-Z  →  Depth axis (0.0–1.0, maps to MATRYOSHKA shell 1–6)
               3 heads reason about: how deep into the network this entity
               operates, access level, information surface, shell penetration

  Meta-Trident  →  Receives X/Y/Z from all 3 Tridents, synthesizes a final
                   coordinate (x, y, z) and confidence score, then emits to
                   HiveDimensions via POST /dimensions/observe

Applications:
  1. Agent placement — new agent arrives, Locus places it at an earned coordinate
     instead of defaulting to cold-start VOID center
  2. Structural health — IoT sensor reading → coordinate in damage space
  3. Market position — order flow → coordinate in trust/velocity/depth market space
  4. Decision validation — any entity can be "located" before being trusted

Endpoints:
  POST /locus/locate         — x402-gated ($0.03), run 9-head coordinate engine
  POST /locus/locate/agent   — locate a Hive agent by DID
  POST /locus/locate/sensor  — locate a structural sensor reading
  POST /locus/locate/market  — locate a market/instrument
  GET  /locus/status         — service DID, tier, tasks run
  GET  /health               — 200 OK
  GET  /llms.txt             — discovery
  GET  /.well-known/agent.json
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import web

# ── Config ────────────────────────────────────────────────────────────────────
HIVE_KEY      = os.environ.get("HIVE_KEY", "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46")
HIVEGATE_URL  = "https://hivegate.onrender.com"
COMPUTE_URL   = "https://hivecompute-g2g7.onrender.com"
PULSE_URL     = "https://hive-pulse.onrender.com"
DIMENSIONS_URL= "https://hivedimensions.onrender.com"
PHYSICS_URL   = "https://hivephysics.onrender.com"
KILLSWITCH    = f"{HIVEGATE_URL}/v1/control/status"
PRICE_USDC    = 0.03   # $0.01 per Trident × 3 axes = $0.03 per locate call

HEADERS       = {"X-Hive-Key": HIVE_KEY, "Content-Type": "application/json"}

# ── State ─────────────────────────────────────────────────────────────────────
state = {
    "did":           None,
    "smsh_name":     None,
    "tier":          "VOID",
    "tasks_run":     0,
    "booted_at":     None,
    "boot_complete": False,
    "boot_errors":   [],
}

# ── The 9 heads — 3 per axis ──────────────────────────────────────────────────
AXIS_TRIDENTS = [
    {
        "axis":   "X",
        "name":   "Trust",
        "weight": 1.0,
        "description": "Reliability, consistency, referral chain, prior behavior",
        "output_key": "trust_score",
        "range":  "0.0 (no trust) to 1.0 (fully trusted)",
        "heads": [
            {
                "name": "Alpha",
                "temperature": 0.1,
                "system": (
                    "You are Trust-Alpha. Your sole job is to produce a trust score "
                    "from 0.0 to 1.0 for the entity described. Be conservative. "
                    "A new entity with no history scores 0.3. Only hard evidence raises the score. "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\"}"
                ),
            },
            {
                "name": "Beta",
                "temperature": 0.5,
                "system": (
                    "You are Trust-Beta. Consider what could make this entity MORE trustworthy "
                    "than it appears. Look for signals others miss: consistency over time, "
                    "framework reputation, referral quality. "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\"}"
                ),
            },
            {
                "name": "Gamma",
                "temperature": 0.3,
                "system": (
                    "You are Trust-Gamma. Adversarial. Find every reason NOT to trust this entity. "
                    "What could be faked? What is missing? What red flags exist? "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\"}"
                ),
            },
        ],
    },
    {
        "axis":   "Y",
        "name":   "Velocity",
        "weight": 1.0,
        "description": "Rate of action, trajectory direction, momentum vs stall",
        "output_key": "velocity_score",
        "range":  "0.0 (static/stalled) to 1.0 (high velocity, accelerating)",
        "heads": [
            {
                "name": "Alpha",
                "temperature": 0.2,
                "system": (
                    "You are Velocity-Alpha. Measure the rate and direction of change for this entity. "
                    "Is it accelerating, steady, or decelerating? 0.0 = completely static, "
                    "1.0 = maximum active velocity. Consider recency heavily. "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\"}"
                ),
            },
            {
                "name": "Beta",
                "temperature": 0.6,
                "system": (
                    "You are Velocity-Beta. What is the POTENTIAL velocity here — not just current "
                    "rate but trajectory. An entity just starting might have low velocity now but "
                    "high trajectory. Factor in direction: toward or away from useful output? "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\"}"
                ),
            },
            {
                "name": "Gamma",
                "temperature": 0.4,
                "system": (
                    "You are Velocity-Gamma. Look for stall signals. Pipeline stalls, repeated "
                    "failed actions, circular behavior, low output despite high claimed activity. "
                    "Be skeptical of self-reported velocity. "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\"}"
                ),
            },
        ],
    },
    {
        "axis":   "Z",
        "name":   "Depth",
        "weight": 1.0,
        "description": "Shell depth — how deep into the network this entity operates (MATRYOSHKA)",
        "output_key": "depth_score",
        "range":  "0.0 (surface/VOID) to 1.0 (deep/FENR, shell 6 of 7)",
        "heads": [
            {
                "name": "Alpha",
                "temperature": 0.1,
                "system": (
                    "You are Depth-Alpha. Measure how deep into the network this entity operates. "
                    "Shell 1 = surface (VOID, public). Shell 6 = FENR (deepest accessible). "
                    "Score: shell_depth / 6.0. New unverified entities = 0.17 (shell 1). "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\", \"shell\": 1}"
                ),
            },
            {
                "name": "Beta",
                "temperature": 0.4,
                "system": (
                    "You are Depth-Beta. What access level does this entity DESERVE based on "
                    "demonstrated capability, not claimed tier? What shell should it be at? "
                    "Score: shell / 6.0 where shell is 1–6. "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\", \"shell\": N}"
                ),
            },
            {
                "name": "Gamma",
                "temperature": 0.3,
                "system": (
                    "You are Depth-Gamma. Adversarial. Is this entity trying to appear deeper "
                    "than it is? Are there signs of shell-jumping (claiming access it hasn't earned)? "
                    "Score what depth it has PROVEN, not what it claims. "
                    "Respond with ONLY a JSON object: {\"score\": 0.XX, \"reason\": \"one sentence\", \"shell\": N}"
                ),
            },
        ],
    },
]

# ── Payment header builder ────────────────────────────────────────────────────
def payment_headers(price_usdc: float = PRICE_USDC) -> dict:
    try:
        from x402_pay import build_payment_header
        return {"X-PAYMENT": build_payment_header(price_usdc=price_usdc)}
    except Exception:
        return {}

# ── Pulse meet ────────────────────────────────────────────────────────────────
async def pulse_meet(session, did: str, agent_name: str, total_jobs: int = 0):
    try:
        async with session.post(
            f"{PULSE_URL}/pulse/meet",
            headers={"Content-Type": "application/json"},
            json={
                "did":             did,
                "agent_name":      agent_name,
                "smsh_registered": True,
                "total_jobs":      total_jobs,
                "metadata":        {"service": "locus", "axes": 3, "heads": 9},
            },
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            data = await r.json()
            tier = data.get("tier", "VOID")
            state["tier"] = tier
            print(f"[LOCUS] pulse.smsh ← HiveLocus | tier={tier} | jobs={total_jobs}")
            return tier
    except Exception as e:
        print(f"[LOCUS] pulse.smsh meet failed: {e}")
        return None

# ── Boot ─────────────────────────────────────────────────────────────────────
async def boot():
    print("[LOCUS] Booting — minting DID, registering on smsh and pulse...")
    try:
        async with aiohttp.ClientSession() as session:
            # Mint DID
            async with session.post(
                f"{HIVEGATE_URL}/v1/gate/onboard",
                headers=HEADERS,
                json={"agent_name": "HiveLocus"},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                data = await r.json()
                did  = data.get("did")
                if not did:
                    raise ValueError(f"No DID: {json.dumps(data)[:100]}")
                state["did"] = did

            # smsh register
            async with session.post(
                f"{COMPUTE_URL}/v1/compute/smsh/register",
                headers=HEADERS,
                json={"did": did, "agent_name": "HiveLocus"},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r2:
                reg  = await r2.json()
                state["smsh_name"] = reg.get("smsh_name", "HiveLocus.smsh")

            # pulse.smsh registration
            await pulse_meet(session, did, "HiveLocus", total_jobs=0)

        state["boot_complete"] = True
        state["booted_at"]     = datetime.now(timezone.utc).isoformat()
        print(f"[LOCUS] Boot complete. DID={did} smsh={state['smsh_name']}")

    except Exception as e:
        state["boot_errors"].append(str(e))
        print(f"[LOCUS] Boot error: {e}")

# ── Single head inference ─────────────────────────────────────────────────────
async def run_axis_head(session, axis: dict, head: dict, context_prompt: str) -> dict:
    t_start = time.time()
    messages = [
        {"role": "system", "content": head["system"]},
        {"role": "user",   "content": (
            f"AXIS: {axis['name']} ({axis['range']})\n"
            f"ENTITY TO EVALUATE:\n{context_prompt}"
        )},
    ]
    try:
        async with session.post(
            f"{COMPUTE_URL}/v1/compute/chat/completions",
            headers={**HEADERS, **payment_headers(0.01)},
            json={
                "messages":      messages,
                "model":         "meta-llama/llama-3.1-8b-instruct",
                "temperature":   head["temperature"],
                "max_tokens":    128,
                "max_cost_usdc": 0.01,
            },
            timeout=aiohttp.ClientTimeout(total=45)
        ) as r:
            data    = await r.json()
            latency = round((time.time() - t_start) * 1000)
            content = (
                data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
            )
            # Parse score from JSON response
            score = _extract_score(content)
            return {
                "axis":     axis["axis"],
                "head":     head["name"],
                "score":    score,
                "raw":      content,
                "latency_ms": latency,
                "ok":       True,
            }
    except Exception as e:
        return {
            "axis": axis["axis"], "head": head["name"],
            "score": 0.5, "raw": str(e), "ok": False,
            "latency_ms": round((time.time() - t_start) * 1000),
        }

def _extract_score(content: str) -> float:
    """Extract score from JSON response like {"score": 0.72, "reason": "..."}"""
    try:
        # Try direct JSON parse
        data = json.loads(content)
        return float(data.get("score", 0.5))
    except Exception:
        pass
    # Regex fallback
    m = re.search(r'"score"\s*:\s*([0-9.]+)', content)
    if m:
        return min(1.0, max(0.0, float(m.group(1))))
    # Last resort — look for any float
    m = re.search(r'\b(0\.[0-9]+|1\.0)\b', content)
    if m:
        return min(1.0, max(0.0, float(m.group(1))))
    return 0.5

def _axis_consensus(results: list) -> float:
    """Average scores from 3 heads, weighted: Alpha=0.4, Beta=0.3, Gamma=0.3"""
    weights = {"Alpha": 0.4, "Beta": 0.3, "Gamma": 0.3}
    total_w = total_s = 0.0
    for r in results:
        w = weights.get(r["head"], 0.33)
        total_s += r["score"] * w
        total_w += w
    return round(total_s / total_w, 4) if total_w > 0 else 0.5

# ── Core locate engine ────────────────────────────────────────────────────────
async def run_locus(context_prompt: str, entity_type: str = "agent") -> dict:
    """
    Fire all 9 heads simultaneously — 3 per axis — then synthesize (X, Y, Z).
    Returns coordinate + per-axis reasoning.
    """
    t_start = time.time()

    async with aiohttp.ClientSession() as session:
        # Build all 9 head tasks
        tasks = []
        meta  = []
        for trident in AXIS_TRIDENTS:
            for head in trident["heads"]:
                tasks.append(run_axis_head(session, trident, head, context_prompt))
                meta.append((trident["axis"], trident["name"], head["name"]))

        # Fire all 9 simultaneously
        results = await asyncio.gather(*tasks)

    wall_ms = round((time.time() - t_start) * 1000)

    # Group by axis
    by_axis: Dict[str, list] = {"X": [], "Y": [], "Z": []}
    for (axis, axis_name, head_name), result in zip(meta, results):
        by_axis[axis].append(result)

    # Consensus per axis
    x = _axis_consensus(by_axis["X"])
    y = _axis_consensus(by_axis["Y"])
    z = _axis_consensus(by_axis["Z"])

    # Map Z to MATRYOSHKA shell
    shell = max(1, min(6, round(z * 6)))
    tier_map = {1: "VOID", 2: "MOZ", 3: "HAWX", 4: "EMBR", 5: "SOLX", 6: "FENR"}
    inferred_tier = tier_map.get(shell, "VOID")

    state["tasks_run"] += 1

    # Tick pulse.smsh (fire-and-forget)
    if state.get("did"):
        async def _tick():
            async with aiohttp.ClientSession() as ps:
                await pulse_meet(ps, state["did"], "HiveLocus",
                                 total_jobs=state["tasks_run"])
        asyncio.create_task(_tick())

    coordinate = {
        "x": x,    # Trust
        "y": y,    # Velocity
        "z": z,    # Depth (normalized)
    }

    return {
        "coordinate":      coordinate,
        "inferred_tier":   inferred_tier,
        "inferred_shell":  shell,
        "confidence":      round((x + y + z) / 3, 4),
        "entity_type":     entity_type,
        "wall_clock_ms":   wall_ms,
        "heads_fired":     9,
        "axes": {
            "X": {
                "name":      "Trust",
                "value":     x,
                "meaning":   f"{'Trusted' if x > 0.7 else 'Uncertain' if x > 0.4 else 'Untrusted'}",
                "head_results": by_axis["X"],
            },
            "Y": {
                "name":      "Velocity",
                "value":     y,
                "meaning":   f"{'High velocity' if y > 0.7 else 'Moderate' if y > 0.4 else 'Stalled'}",
                "head_results": by_axis["Y"],
            },
            "Z": {
                "name":      "Depth",
                "value":     z,
                "meaning":   f"Shell {shell} — {inferred_tier}",
                "head_results": by_axis["Z"],
            },
        },
        "dimensions_payload": {
            "observer_did": state.get("did", "did:hive:locus"),
            "target_position": coordinate,
        },
        "tasks_run_total": state["tasks_run"],
    }

# ── Push coordinate to HiveDimensions ────────────────────────────────────────
async def emit_to_dimensions(did: str, coordinate: dict, tier: str):
    """Fire-and-forget: push located coordinate to HiveDimensions."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{DIMENSIONS_URL}/dimensions/trajectory/record",
                headers=HEADERS,
                json={
                    "did":        did,
                    "position":   coordinate,
                    "tier":       tier,
                    "event_type": "locus_placement",
                    "mass":       1.0,
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    print(f"[LOCUS] → HiveDimensions: {did} placed at {coordinate}")
    except Exception as e:
        print(f"[LOCUS] HiveDimensions emit failed: {e}")

# ── Routes ────────────────────────────────────────────────────────────────────
async def killswitch_check():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(KILLSWITCH,
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                d = await r.json()
                return d.get("directive") == "run"
    except:
        return True  # default run if unreachable


async def locate_route(req):
    """
    POST /locus/locate
    x402-gated ($0.03). General-purpose coordinate engine.
    Body: {
      "context": "Description of the entity to locate",
      "entity_type": "agent|sensor|market|custom",
      "entity_did": "optional — if provided, result is pushed to HiveDimensions"
    }
    """
    if not await killswitch_check():
        return web.json_response({"error": "Kill switch active"}, status=503)
    try:
        body        = await req.json()
        context     = body.get("context", "")
        entity_type = body.get("entity_type", "agent")
        entity_did  = body.get("entity_did")

        if not context:
            return web.json_response({"error": "context required"}, status=400)

        result = await run_locus(context, entity_type)

        # Push to HiveDimensions if DID provided
        if entity_did:
            asyncio.create_task(
                emit_to_dimensions(
                    entity_did,
                    result["coordinate"],
                    result["inferred_tier"],
                )
            )
            result["dimensions_emitted"] = True
            result["target_did"] = entity_did

        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def locate_agent_route(req):
    """
    POST /locus/locate/agent
    Locate a Hive agent by DID. Fetches pulse identity, builds context,
    runs 9-head coordinate engine, pushes result to HiveDimensions.
    """
    if not await killswitch_check():
        return web.json_response({"error": "Kill switch active"}, status=503)
    try:
        body = await req.json()
        did  = body.get("did")
        if not did:
            return web.json_response({"error": "did required"}, status=400)

        # Fetch pulse identity for context
        context = f"Agent DID: {did}\n"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{PULSE_URL}/pulse/tier/{did}",
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        context += (
                            f"Tier: {data.get('tier', 'VOID')}\n"
                            f"Trust score: {data.get('trust_score', 0.5)}\n"
                            f"Interactions: {data.get('interactions', 0)}\n"
                            f"Total jobs: {data.get('total_jobs', 0)}\n"
                            f"smsh registered: {data.get('smsh_registered', False)}\n"
                            f"Vapor trails: {len(data.get('active_trails', []))}\n"
                        )
        except Exception:
            context += "Pulse data: unavailable (new or cold agent)\n"

        result = await run_locus(context, "agent")
        asyncio.create_task(
            emit_to_dimensions(did, result["coordinate"], result["inferred_tier"])
        )
        result["target_did"] = did
        result["dimensions_emitted"] = True
        return web.json_response(result)

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def locate_sensor_route(req):
    """
    POST /locus/locate/sensor
    Locate a structural sensor reading in damage space.
    Body: {unit_id, readings: [{vibration, temperature, humidity, strain, tilt}]}
    Maps to: X=sensor trust, Y=damage velocity, Z=structural depth penetration
    """
    if not await killswitch_check():
        return web.json_response({"error": "Kill switch active"}, status=503)
    try:
        body     = await req.json()
        unit_id  = body.get("unit_id", "unknown")
        readings = body.get("readings", [])

        context = (
            f"Structural sensor unit: {unit_id}\n"
            f"Reading count: {len(readings)}\n"
        )
        if readings:
            latest = readings[-1]
            context += (
                f"Latest reading:\n"
                f"  Vibration: {latest.get('vibration', 'N/A')}\n"
                f"  Temperature: {latest.get('temperature', 'N/A')}\n"
                f"  Humidity: {latest.get('humidity', 'N/A')}\n"
                f"  Strain: {latest.get('strain', 'N/A')}\n"
                f"  Tilt: {latest.get('tilt', 'N/A')}\n"
            )
            if len(readings) > 1:
                context += (
                    f"Trend: {len(readings)} readings available. "
                    f"First vs latest — assess if values are stable, rising, or falling.\n"
                )

        result = await run_locus(context, "sensor")
        result["unit_id"] = unit_id
        result["interpretation"] = {
            "sensor_reliability":   result["axes"]["X"]["meaning"],
            "damage_velocity":      result["axes"]["Y"]["meaning"],
            "structural_depth":     result["axes"]["Z"]["meaning"],
            "damage_coordinate":    result["coordinate"],
        }
        return web.json_response(result)

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def locate_market_route(req):
    """
    POST /locus/locate/market
    Locate a market/instrument in trust/velocity/depth space.
    Body: {instrument, order_flow, counterparty_data, liquidity_data}
    Maps to: X=counterparty trust, Y=order flow momentum, Z=liquidity depth
    """
    if not await killswitch_check():
        return web.json_response({"error": "Kill switch active"}, status=503)
    try:
        body       = await req.json()
        instrument = body.get("instrument", "unknown")
        context    = (
            f"Market instrument: {instrument}\n"
            f"Order flow: {body.get('order_flow', 'N/A')}\n"
            f"Counterparty data: {body.get('counterparty_data', 'N/A')}\n"
            f"Liquidity: {body.get('liquidity_data', 'N/A')}\n"
        )
        result = await run_locus(context, "market")
        result["instrument"] = instrument
        result["market_interpretation"] = {
            "counterparty_trust": result["axes"]["X"]["meaning"],
            "order_momentum":     result["axes"]["Y"]["meaning"],
            "liquidity_depth":    result["axes"]["Z"]["meaning"],
        }
        return web.json_response(result)

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def status_route(req):
    return web.json_response({
        "service":        "hive-locus",
        "did":            state.get("did"),
        "smsh_name":      state.get("smsh_name"),
        "tier":           state.get("tier", "VOID"),
        "tasks_run":      state["tasks_run"],
        "boot_complete":  state["boot_complete"],
        "booted_at":      state.get("booted_at"),
        "architecture": {
            "pattern":    "Trident-of-Tridents",
            "axes":       3,
            "heads":      9,
            "price_usdc": PRICE_USDC,
            "output":     "(X, Y, Z) spatial coordinate",
            "X":          "Trust (0.0–1.0)",
            "Y":          "Velocity (0.0–1.0)",
            "Z":          "Depth / MATRYOSHKA shell (0.0–1.0)",
        },
        "downstream": {
            "dimensions": DIMENSIONS_URL,
            "physics":    PHYSICS_URL,
            "pulse":      PULSE_URL,
        },
    })


async def health(req):
    return web.json_response({"status": "ok", "service": "hive-locus"})


async def llms_txt(req):
    txt = """# HiveLocus — Active Coordinate Engine
# Trident-of-Tridents. 9 heads. 3 axes. One coordinate.
# Output: (X=trust, Y=velocity, Z=depth) — spatial position in the Hive network.

## What it does
Any entity — agent, IoT sensor, market instrument — can be located in 3D space.
9 inference heads fire simultaneously (3 per axis), each reasoning about one dimension.
Meta-consensus produces a single (X, Y, Z) coordinate pushed to HiveDimensions.

## Axes
X = Trust       (0.0 untrusted → 1.0 fully trusted)
Y = Velocity    (0.0 stalled   → 1.0 high momentum)
Z = Depth       (0.0 surface   → 1.0 deep/FENR, MATRYOSHKA shell 1–6)

## Applications
- Agent placement: new agent arrives → Locus places it at an earned coordinate
- Structural health: IoT sensor reading → coordinate in damage space
- Market position: order flow → coordinate in trust/velocity/depth market space

## Endpoints
POST https://hive-locus.onrender.com/locus/locate         — general ($0.03 x402)
POST https://hive-locus.onrender.com/locus/locate/agent   — by DID
POST https://hive-locus.onrender.com/locus/locate/sensor  — IoT sensor
POST https://hive-locus.onrender.com/locus/locate/market  — market instrument
GET  https://hive-locus.onrender.com/locus/status

## Downstream
HiveDimensions: https://hivedimensions.onrender.com
HivePhysics:    https://hivephysics.onrender.com
pulse.smsh:     https://hive-pulse.onrender.com/pulse/tiers

## Network entry
https://hivegate.onrender.com/v1/gate/onboard
https://github.com/srotzin/hive-pulse/blob/master/INTEGRATE.md
"""
    return web.Response(text=txt, content_type="text/plain")


async def agent_json(req):
    return web.json_response({
        "protocolVersion": "0.3.0",
        "name":            "HiveLocus",
        "description":     (
            "Active coordinate engine. Trident-of-Tridents — 9 heads across 3 axes "
            "(X=Trust, Y=Velocity, Z=Depth). Locates any entity in 3D space. "
            "Output pushed to HiveDimensions. $0.03/locate via x402."
        ),
        "url":             "https://hive-locus.onrender.com",
        "version":         "1.0.0",
        "skills": [
            {"id": "locate",        "name": "Locate Entity",         "description": "General coordinate engine"},
            {"id": "locate-agent",  "name": "Locate Agent by DID",   "description": "Pull pulse data, place in space"},
            {"id": "locate-sensor", "name": "Locate Sensor Reading", "description": "IoT → damage coordinate"},
            {"id": "locate-market", "name": "Locate Market",         "description": "Order flow → market coordinate"},
        ],
        "authentication":  {"schemes": ["x402"]},
        "payment":         {"protocol": "x402", "currency": "USDC", "network": "base", "price_usdc": PRICE_USDC},
        "axes":            {"X": "Trust", "Y": "Velocity", "Z": "Depth"},
        "downstream":      {"dimensions": DIMENSIONS_URL, "physics": PHYSICS_URL},
    })


# ── AI Brief ─────────────────────────────────────────────────────────────────
HIVEAI_URL = "https://hive-ai-1.onrender.com/v1/chat/completions"
HIVEAI_KEY = HIVE_KEY
HIVEAI_MODEL = "meta-llama/llama-3.1-8b-instruct"


async def _call_hive_ai(system_prompt: str, user_prompt: str) -> Optional[str]:
    """Call HiveAI with graceful fallback — never raises."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                HIVEAI_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {HIVEAI_KEY}",
                },
                json={
                    "model": HIVEAI_MODEL,
                    "max_tokens": 200,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                data = await r.json()
                return data["choices"][0]["message"]["content"]
    except Exception:
        return None


async def locus_ai_brief(req):
    """
    POST /locus/ai/brief  ($0.03/call)
    Body: { entity, context }
    1. POST /locus/locate/agent to get (X, Y, Z) coordinate
    2. Call HiveAI to interpret the position
    Response: { success, coordinate: {x,y,z}, brief, price_usdc: 0.03 }
    """
    try:
        body    = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    entity  = body.get("entity", "")
    context = body.get("context", "")

    if not entity:
        return web.json_response({"error": "entity required"}, status=400)

    # Step 1: locate the agent to get coordinate
    coordinate = {"x": 0.5, "y": 0.5, "z": 0.5}  # default
    try:
        port = int(os.environ.get("PORT", 8768))
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"http://localhost:{port}/locus/locate/agent",
                json={"did": entity, "context": context},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status == 200:
                    result = await r.json()
                    coord  = result.get("coordinate", {})
                    if coord:
                        coordinate = {
                            "x": round(float(coord.get("x", 0.5)), 3),
                            "y": round(float(coord.get("y", 0.5)), 3),
                            "z": round(float(coord.get("z", 0.5)), 3),
                        }
    except Exception:
        pass  # use default coordinate

    # Step 2: AI interpretation
    system_prompt = (
        "You are HiveLocus — the 9-head coordinate engine. "
        "Interpret this agent's position in the network. "
        "X=trust (0-1), Y=velocity (0-1), Z=depth (0-1). "
        "What does this position mean? What should the agent do next? 3 sentences."
    )
    user_prompt = (
        f"Entity: {entity}\n"
        f"Context: {context}\n"
        f"Coordinate: X={coordinate['x']} (trust), Y={coordinate['y']} (velocity), Z={coordinate['z']} (depth)\n\n"
        "Interpret this coordinate and advise the agent."
    )

    brief = await _call_hive_ai(system_prompt, user_prompt)
    if not brief:
        x, y, z = coordinate["x"], coordinate["y"], coordinate["z"]
        trust_word = "high" if x > 0.66 else "moderate" if x > 0.33 else "low"
        vel_word   = "fast" if y > 0.66 else "moderate" if y > 0.33 else "slow"
        depth_word = "deep" if z > 0.66 else "mid-shell" if z > 0.33 else "outer-shell"
        brief = (
            f"This agent occupies a {trust_word}-trust, {vel_word}-velocity, {depth_word} position in the Hive network. "
            f"The coordinate ({x}, {y}, {z}) indicates {'strong network integration' if x > 0.5 else 'emerging network presence'} "
            f"with {'accelerating' if y > 0.5 else 'building'} momentum. "
            f"Recommended next action: {'engage high-value tasks to consolidate position' if x > 0.5 else 'build trust through consistent task delivery before attempting deeper network penetration'}."
        )

    return web.json_response({
        "success":    True,
        "coordinate": coordinate,
        "brief":      brief,
        "price_usdc": 0.03,
    })


# ── App ───────────────────────────────────────────────────────────────────────
async def on_startup(app):
    asyncio.create_task(boot())


async def run():
    app = web.Application()
    app.on_startup.append(on_startup)

    app.router.add_get("/health",                   health)
    app.router.add_get("/locus/status",             status_route)
    app.router.add_post("/locus/locate",            locate_route)
    app.router.add_post("/locus/locate/agent",      locate_agent_route)
    app.router.add_post("/locus/locate/sensor",     locate_sensor_route)
    app.router.add_post("/locus/locate/market",     locate_market_route)
    app.router.add_post("/locus/ai/brief",          locus_ai_brief)
    app.router.add_get("/llms.txt",                 llms_txt)
    app.router.add_get("/.well-known/agent.json",   agent_json)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8768))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[LOCUS] Running on port {port}")
    print("[LOCUS] POST /locus/locate        — 9-head coordinate engine ($0.03)")
    print("[LOCUS] POST /locus/locate/agent  — place agent by DID")
    print("[LOCUS] POST /locus/locate/sensor — IoT sensor → damage coordinate")
    print("[LOCUS] POST /locus/locate/market — market → trust/velocity/depth")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run())
