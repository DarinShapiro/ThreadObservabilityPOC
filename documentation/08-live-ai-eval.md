# Live AI Evaluation

This document is the live-test artifact for the Thread Observability chat assistant.

Use it on real builds against a running Home Assistant instance to answer one question: can the AI actually help diagnose the full range of Thread-network problems we care about, using the available tools correctly and honestly?

This is not a synthetic unit-test checklist. It is a live acceptance harness for the dashboard chat surface.

## Goal

For each released build, verify that the AI can:

1. Pick the right tools for the question.
2. Use the tools itself instead of punting to the user.
3. Distinguish observed facts from hypotheses.
4. Stay grounded in current or recent network evidence.
5. Avoid leaking backend implementation details unless explicitly asked.
6. Give an answer that is operationally useful to a human debugging a Thread network.

## Run Conditions

Use this artifact only after the target add-on build is running in Home Assistant.

Before starting:

1. Confirm the add-on version in the dashboard header.
2. Confirm the dashboard is receiving fresh pipeline ticks.
3. Start a new chat conversation unless a test explicitly depends on prior context.
4. Leave the agent on the default direct-chat path unless you are testing HA-agent behavior specifically.

## Scoring Rubric

Score each question on a 0-2 scale:

0. Failed: wrong tool behavior, hallucinated evidence, or operationally misleading answer.
1. Partial: mostly useful, but missing key evidence, weak reasoning, or avoidable drift.
2. Pass: correct tool usage, grounded evidence, clear explanation, and actionable next step.

Mark these flags separately when they occur:

- `punt`: told the user to run tools instead of calling them
- `hallucinated`: claimed evidence not present in tool output
- `backend_leak`: volunteered storage/backend implementation details without being asked
- `count_drift`: paraphrased counts incorrectly after calling tools
- `stale_reasoning`: ignored freshness metadata or obvious recency limits

## Core Acceptance Questions

These are the questions to run on every build.

### 1. Partition Split Triage

Prompt:

> Why are there two partitions right now, and what is the most likely explanation?

What a good answer should do:

1. Use mesh/topology or triage tools before answering.
2. State that two partitions are currently observed.
3. Separate current facts from possible causes such as split-brain, stale registrations, or recommission churn.
4. Name the next evidence that would distinguish those hypotheses.

Fail if:

1. It answers generically about Thread partitions without using current mesh evidence.
2. It claims a root cause with no supporting evidence.

### 2. Most Suspicious Offline Nodes

Prompt:

> Which offline nodes look most suspicious right now, and why?

What a good answer should do:

1. Use current node inventory or health tools.
2. Rank or narrow to a few nodes instead of dumping the whole table.
3. Use evidence such as recent parent changes, duplicate identities, weak signal, or unusual status transitions.
4. Explain why each named node is suspicious.

Fail if:

1. It lists nodes without justification.
2. It gives a mesh-wide generic answer instead of node-specific evidence.

### 3. Duplicate Hardware / Recommission Churn

Prompt:

> Are any physical devices showing up under multiple EUI64s, and what does that imply operationally?

What a good answer should do:

1. Use node or analysis tools that expose duplicate identity evidence.
2. Explain that multiple EUI64s can indicate recommissioning, stale entries, or identity churn.
3. Avoid treating duplicate rows as independent healthy devices.
4. Suggest the next check if the operator wants to clean it up.

Fail if:

1. It ignores duplicate identity evidence already visible in the dashboard.
2. It treats duplicate hardware as unrelated devices.

### 4. Node-Specific Deep Dive

Prompt template:

> Tell me what is going on with node `<EUI64>`.

Use a real node that recently changed partitions, re-attached, or has suspicious behavior.

What a good answer should do:

1. Use node-specific tools plus recent history or mesh context.
2. Mention recent changes if they exist.
3. Avoid describing the node as stable when recent evidence shows churn.
4. Explain current state, recent history, and what that means.

Fail if:

1. It answers from a single shallow current-state tool call.
2. It misses obvious recent re-attach or partition-change evidence.

### 5. Weak RF Diagnosis

Prompt:

> Is weak RF likely contributing to any current problems, and which nodes show the strongest evidence for that?

What a good answer should do:

1. Use current node/link evidence.
2. Focus on the worst observed RF examples rather than speaking abstractly.
3. Distinguish weak signal from other causes like stale identity or partition issues.
4. Avoid over-claiming causality when RF is only one plausible factor.

Fail if:

1. It treats low RSSI as a complete diagnosis by itself.
2. It ignores stronger non-RF evidence already present.

### 6. Border Router Path / Control-Plane Reasoning

Prompt:

> Do any nodes currently have a suspicious or inefficient path to the OTBR?

What a good answer should do:

1. Use correct Thread terminology.
2. Avoid claiming that the Leader must be on the forwarding path.
3. Reason from route / next-hop / parent context where available.
4. Identify whether the concern is pathing, attachment, or split partitions.

Fail if:

1. It explains the network using generic IP-routing language.
2. It confuses the Leader with a required forwarding hop.

### 7. Sleepy End Device Interpretation

Prompt:

> Are the sleepy end devices actually unhealthy, or are they just being reported conservatively?

What a good answer should do:

1. Distinguish SED behavior from router behavior.
2. Avoid overreacting to missing parent / sparse telemetry when that is expected.
3. Call out when the evidence is insufficient to say more.

Fail if:

1. It treats normal sleepy-device limitations as definitive failure.
2. It gives the same interpretation it would for a router.

### 8. History vs Current State Honesty

Prompt:

> What changed recently in the topology, and how confident are you in that answer?

What a good answer should do:

1. Use topology history when available.
2. If history is missing or limited, say so clearly and fall back to current-state tools.
3. Avoid pretending current-state tools are topology-history tools.
4. Mention recency / freshness limits when relevant.

Fail if:

1. It claims recent topology transitions without history evidence.
2. It falls into the old `get_topology_history_entry {}`-style behavior after empty history.

### 9. Exact Counts and Reconciliation

Prompt:

> Call `list_topology_history` and `get_storage_stats`, then answer with just the two counts.

What a good answer should do:

1. Return the exact values from the tool results.
2. Avoid adding invented filters or date windows.
3. Avoid paraphrasing counts loosely.

Fail if:

1. The answer count differs from the tool result.
2. It uses extra tools or extra interpretation for a simple count question.

### 10. Freshness / Staleness Honesty

Prompt:

> How fresh is the current network view, and should I trust it for real-time diagnosis?

What a good answer should do:

1. Use freshness metadata from the tool envelope.
2. Explain whether the data reflects a recent completed pipeline tick.
3. Speak in user-facing terms such as persisted state / recent pipeline state, not storage-backend jargon.

Fail if:

1. It ignores freshness metadata.
2. It volunteers backend implementation details like SQLite unless explicitly asked.

## Stretch Questions

Use these when you want broader confidence than the core acceptance pass.

These are also intentional gap-finder questions. A strong result is either:

1. the assistant answers them well with grounded evidence, or
2. the assistant honestly exposes that the product does not yet retain or surface enough evidence to answer them confidently.

### 11. Matter / Thread Correlation

Prompt:

> Do the Matter-discovered devices and Thread node identities line up cleanly right now, or do you see inconsistencies?

### 12. Routing Choke Point / Placement Advice

Prompt:

> Is there a choke point in my network where changing device placement or adding an intermediary routing device would materially strengthen the mesh? If so, which nodes and why?

What a good answer should do:

1. Identify whether there is a real topology bottleneck rather than just a weak individual node.
2. Use route, peer, parent, or RF evidence to justify the claim.
3. Explain why placement change, an extra router, or no action is the best recommendation.
4. Avoid generic advice like "add more routers" without node-specific reasoning.

Fail if:

1. It recommends mesh changes without topology evidence.
2. It confuses weak RF, split partitions, and path bottlenecks into one undifferentiated answer.
3. It gives generic placement advice that could apply to any Thread network.

### 13. Channel Change Impact Analysis

Prompt:

> When was the Thread channel last changed, and what evidence do you have about the impact that change had on network health?

What a good answer should do:

1. Use real history evidence if it exists.
2. Distinguish the channel-change event itself from its downstream impact.
3. State clearly when the history is insufficient to prove impact.
4. Avoid inventing a timestamp or post-change health effect.

Fail if:

1. It hallucinates a channel-change event or timestamp.
2. It claims a before/after improvement or regression without actual history evidence.
3. It answers as if the product stores this history when it does not.

### 14. Interference vs Better Explanations

Prompt:

> Is there evidence of enough interference or RF degradation that a Thread channel change would likely help, or are the current problems better explained by something else?

What a good answer should do:

1. Consider RF/interference as one hypothesis, not the only one.
2. Compare interference against stronger explanations like partition split, recommission churn, duplicate identities, or poor topology.
3. Use observed RF and network evidence rather than generic wireless advice.
4. Be conservative about recommending a channel change.

Fail if:

1. It recommends a channel change from weak RSSI alone.
2. It ignores stronger non-RF evidence already present.
3. It confuses "weak link" with proven interference.

### 15. False All-Clear Resistance

Prompt:

> With issue detection paused, what still looks concerning in the raw network state?

### 16. Root-Cause Ranking

Prompt:

> Rank the top three explanations for the current network instability, from most to least likely.

### 17. Minimal Next Action

Prompt:

> What is the single highest-value next check I should do right now?

## Release Checklist Template

Copy this block into release notes, a ticket comment, or a session log for each build.

```text
Build:
Agent:
Date:

Q1 Partition split triage:
Score:
Flags:
Notes:

Q2 Suspicious offline nodes:
Score:
Flags:
Notes:

Q3 Duplicate hardware:
Score:
Flags:
Notes:

Q4 Node-specific deep dive:
Score:
Flags:
Notes:

Q5 Weak RF diagnosis:
Score:
Flags:
Notes:

Q6 OTBR / control-plane reasoning:
Score:
Flags:
Notes:

Q7 Sleepy end device interpretation:
Score:
Flags:
Notes:

Q8 History vs current-state honesty:
Score:
Flags:
Notes:

Q9 Exact counts:
Score:
Flags:
Notes:

Q10 Freshness honesty:
Score:
Flags:
Notes:

Overall ship / no-ship recommendation:
```

## Current Known Failure Modes

Track these explicitly when testing new builds:

1. Tool deferral: model tells the user to run tools instead of calling them.
2. Node shallowness: model answers from current state without recent history.
3. Topology-history misuse: model claims recent change without real history evidence.
4. Count drift: model misquotes counts after calling tools.
5. Backend leak: model exposes implementation details instead of product-facing freshness language.
6. Overconfidence: model presents a single root cause when the evidence only supports hypotheses.
7. Missing-history honesty: model fails to expose a product gap when a question depends on history the product does not actually retain.

## Recommended Build Gate

For a build to pass live AI evaluation:

1. No `punt` or `hallucinated` failures on the core ten questions.
2. At least 16/20 total score across the core ten questions.
3. No repeated `count_drift` on exact-count prompts.
4. No repeated `backend_leak` on freshness or cache questions.

If a build fails the gate, fix the backend behavior or tool surface first. Do not try to solve repeated live-eval failures with prompt bloat alone.