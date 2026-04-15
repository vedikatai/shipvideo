Use this “speak out loud” version for the top functions.

## 1. `webhook()` in `app/webhook.py`
“`webhook()` is the main entrypoint of the system. It receives the GitHub event, verifies it, decides whether this PR should trigger a run, and then starts the pipeline in the background. It matters because this is where the whole system begins.”

## 2. `analyze_pr()` in `app/steps/pipeline.py`
“`analyze_pr()` handles the planning side of the pipeline. It fetches the PR diff, builds a static contract, checks whether the change is worth processing, and then asks the planner to generate steps. It matters because it turns a raw PR into a structured candidate demo flow.”

## 3. `run_pipeline()` in `app/steps/pipeline.py`
“`run_pipeline()` handles the execution side after planning is done. It runs capture, checks whether the result is sendable, renders the video, uploads it, and records metrics. It matters because it ties the whole end-to-end workflow together.”

## 4. `generate_steps_from_diff()` in `app/steps/step_generation.py`
“`generate_steps_from_diff()` is the main planning function. It combines the PR diff, the contract, and live DOM context from the preview app, then uses the LLM to generate structured UI steps. It matters because this is where the system turns code changes into an executable browser plan.”

## 5. `_call_llm()` in `app/steps/step_generation.py`
“`_call_llm()` is the wrapper around the model call. Its job is to request schema-constrained JSON instead of free-form text and parse the result safely. It matters because it narrows the model’s output space and reduces hallucination.”

## 6. `_run_extraction_phase()` in `app/steps/step_generation.py`
“`_run_extraction_phase()` is the first LLM pass. It extracts structured journey hints like the start route, click labels, terminal marker, and interaction hints from the diff. It matters because it creates the basic flow skeleton before the full step plan is generated.”

## 7. `crawl_dom_data()` in `app/steps/dom_crawler.py`
“`crawl_dom_data()` opens the live preview app with Playwright and collects real UI context like routes, buttons, links, and test IDs. It matters because the planner is grounded in the real UI instead of guessing from code alone.”

## 8. `preflight_gate()` in `app/steps/preflight.py`
“`preflight_gate()` checks whether the generated plan is safe and complete before execution. It verifies the start route, required clicks, terminal assertion, and proof conditions. It matters because it catches weak plans before the browser starts clicking.”

## 9. `run_capture()` in `app/steps/step_execution.py`
“`run_capture()` is the bridge between planning and runtime execution. It chooses the backend, runs the steps, and then decides whether the captured result is good enough to publish. It matters because it turns a plan into actual execution output.”

## 10. `_build_render_approval()` in `app/steps/step_execution.py`
“`_build_render_approval()` decides whether a run is actually sendable. It checks things like approved frames, wrong clicks, target markers, and proof conditions. It matters because the system should not publish a misleading demo.”

## 11. `run_ab_stepwise()` in `app/execution/step_runner.py`
“`run_ab_stepwise()` is the main execution loop for the Agent Browser path. It processes steps one by one, resolves targets on the current page, performs actions, and validates the outcome. It matters because this is the core runtime engine.”

## 12. `_resolve_ab_click_target()` in `app/execution/step_runner.py`
“`_resolve_ab_click_target()` chooses which actual UI element should be clicked on the live page. It uses semantic matching like labels, test IDs, aria labels, and ref selection. It matters because clicking the right thing is one of the hardest parts of the system.”

## 13. `_run_ab_click_attempt()` in `app/execution/step_runner.py`
“`_run_ab_click_attempt()` performs one full click attempt. It snapshots the page before the click, resolves the target, checks if it is actionable, performs the click, snapshots again, and then validates the result. It matters because it is the full click-and-prove cycle.”

## 14. `_evaluate_click_validation()` in `app/execution/step_runner.py`
“`_evaluate_click_validation()` checks whether a click actually caused the expected UI change, like a URL change, text appearing, or an element becoming visible. It matters because a successful click is not enough; the UI must move in the intended direction.”

## 15. `validate_step_against_dom()` in `app/policy/selector_validator.py`
“`validate_step_against_dom()` checks whether a planned target is safe and grounded in the real DOM. It prefers semantic targets like labels, test IDs, and aria labels, and rejects weak selectors. It matters because it helps avoid brittle or invented targeting.”

## 16. `derive_intent()` in `app/browser/ref_selector.py`
“`derive_intent()` turns a click step into a simple semantic intention, like ‘Settings’ or ‘Generate API Key.’ It matters because the runtime needs to understand what the step is trying to do before choosing an element.”

## 17. `select_ref()` in `app/browser/ref_selector.py`
“`select_ref()` takes that intent and matches it against the live Agent Browser snapshot to choose the best element ref. It matters because it turns semantic meaning into an actual runtime target.”

## 18. `render_video()` in `app/render.py`
“`render_video()` takes approved screenshots and uses ffmpeg to build the final MP4. It matters because only trusted frames become the final artifact.”

## 19. `upload_video()` in `app/storage.py`
“`upload_video()` uploads the rendered MP4 to Cloudflare R2 and returns a public URL. It matters because this is how the final artifact leaves the worker and becomes shareable.”

## 20. `comment_on_pr()` in `app/github_comment.py`
“`comment_on_pr()` posts the final success or failure result back to the pull request. It matters because this closes the loop and delivers the output where engineers are already working.”

## Easiest speaking formula
For any function, say:

- “This function lives in `X`.”
- “Its job is `Y`.”
- “It takes `A` and returns/produces `B`.”
- “It matters because `Z`.”

Example:
“`generate_steps_from_diff()` lives in `step_generation.py`. Its job is to turn the PR diff plus live DOM context into structured UI steps. It takes diff files and preview context, and returns a validated step plan plus generation context. It matters because this is the core planning stage of the system.”

If you want, I can make a second version that is:
- only top 8 functions
- or “most likely interview functions only.”