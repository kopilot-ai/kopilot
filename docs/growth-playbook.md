# Kopilot Growth Playbook

This document turns the new trust and proof assets into a repeatable
founder-led content loop and a simple design-partner funnel.

## Funnel

1. **Homepage proof CTA**
   - Event: `cta_hero_cost_workflow`
   - Goal: move visitors from broad curiosity to a specific cost-optimization proof surface
2. **Use-case depth**
   - Event: `cta_proof_use_cases`
   - Goal: show real prompts, reasoning paths, recommendations, and safety boundaries
3. **Install path**
   - Events: `cta_hero_install`, `cta_trust_install`, `cta_cost_use_cases_quick_start`
   - Goal: convert trust into action
4. **Design-partner path**
   - Events: `cta_banner_design_partner`, `cta_design_partner_apply`, `cta_design_partner_request`
   - Goal: capture operators with real workflow pain

## What To Publish Every Week

### Lane 1: Cost Optimization Proof

- "What over-provisioned staging workers actually look like in Kubernetes"
- "Three signs a PVC is probably orphaned, and why you still should not auto-delete it"
- "How we rank cost-saving recommendations without skipping approval gates"
- "The difference between an idle resource review and an unsafe cleanup bot"
- "A real prompt for finding Kubernetes waste before the invoice shows it"

### Lane 2: Agent Ecosystem / Modernization

- "Why MCP matters for infrastructure agents"
- "What portable skills should look like in Kubernetes operations"
- "Where approval gates belong in ACP-era automation"
- "Why YAML skills plus CRDs are a useful bridge to broader agent ecosystems"
- "What we will not automate, even as infra agents get more capable"

## Asset Reuse Map

- `docs/assets/kopilot-cost-demo.svg`
  - Homepage proof visual
  - LinkedIn image post
  - README proof asset
- `docs/assets/kopilot-architecture.svg`
  - README trust asset
  - architecture explainer post
  - docs context image
- `kopilot-site/docs/cost-optimization.html`
  - long-form proof page
  - source material for short posts and demo scripts

## Founder Post Template

1. Hook with the operator pain.
2. Show one real prompt.
3. Show the evidence the agent inspected.
4. Explain the recommendation.
5. End with the safety boundary.
6. Link to the proof page or install path, not just the homepage.

## Design Partner Intake Prompt

Ask for:

- Cluster environment and scale
- Specific operator pain
- Current manual workflow or runbook
- What the agent must never do automatically
- Preferred success signal: time saved, waste found, review quality, or incident clarity

## Release Rhythm

- Publish one proof-backed post per week
- Publish one architecture or interoperability post every two weeks
- Refresh the public roadmap and changelog whenever a new trust or interop surface ships
