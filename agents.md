# Agent instructions — shipvideo-engine

## Project Overview

**Product goal:** Automatically generate high-quality  and accurate demo videos from pull requests: capture real UI behavior (browser automation), summarize changes with an LLM, add narration (TTS), render with FFmpeg, and post the video back to the PR—so teams get accurate feature demos in CI without manual recording or editing.Remeber we are buuilding a mvp right now which proves the product will work right now our main goal is video accuracy 

## Code map (short)

| Area | Files | Role |
|------|--------|------|
| **Pipeline & PR flow** | `app/steps/pipeline.py`, `app/webhook.py`, `app/trigger.py`, `app/job_runner.py` | Orchestrate analyze → steps → capture → render; HTTP webhook; job entry. |
| **Step generation** | `app/steps/step_generation.py`, `app/llm/step_generator.py`, `app/llm/retry_engine.py` | LLM extraction + planning; JSON schemas; retries. |
| **Contracts & extraction** | `app/steps/demo_contract.py`, `app/steps/contract_extraction.py`, `app/steps/pr_extraction.py` | Demo contracts; PR/contract parsing. |
| **Normalization & gates** | `app/steps/step_normalizer.py`, `app/steps/preflight.py`, `app/steps/errors.py` | DOM reconciliation; pre-execution gate; `ContractIntegrityError`. |
| **Metrics** | `app/steps/metrics.py` | Per-run JSON metrics (preflight, terminal, clicks, video usable). |
| **Browser automation** | `app/browser/agent_browser_cli.py`, `app/browser/ref_selector.py`, `app/browser/agent_browser_types.py` | Agent Browser CLI wrapper; ref selection; types. |
| **Execution** | `app/execution/step_runner.py`, `app/steps/step_execution.py` | Run planned steps against live browser; wiring. |
| **DOM / context** | `app/steps/dom_crawler.py`, `app/context/dom_extractor.py`, `app/dom_schema.py` | Crawl DOM; extract context for steps. |
| **Rendering & script** | `app/render.py`, `app/generator/script_generator.py`, `app/script_pipeline.py` | Video assembly; narration script. |
| **Recording** | `app/recorder/playwright_runner.py`, `app/recorder/video_processor.py` | Capture / post-process footage. |
| **Config & storage** | `app/config.py`, `app/config_types.py`, `app/storage.py` | Settings; blobs/artifacts. |
| **Observability** | `observability/tracing.py`, `observability/__init__.py` | OpenTelemetry spans and events. |
| **Integrations** | `app/github_comment.py`, `app/preview_url_resolver.py` | PR comments; preview URL resolution. |

**Goal:** Maintain clean, scalable, production-ready code.

## Agent Behavior

When given a task:

1. Understand relevant files first.
2. Make minimal, focused changes.
3. Explain reasoning briefly and clearly.
4. Ensure code compiles and tests pass.
-No need to add comment anywhere just write code 

## Communication Style

- Keep responses concise and to the point.
- Avoid unnecessary explanations.
- Prefer short answers unless more detail is explicitly requested.
- Minimize token usage while maintaining clarity.
