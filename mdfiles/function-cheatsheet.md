# ShipVideo Function Cheatsheet

Compact interview/debugging cheat sheet for the runtime-critical functions.

## `app/webhook.py`

- `on_startup()`
  - Initializes tracing when the FastAPI app starts.

- `get_video(request)`
  - Serves the rendered `out.mp4` file with range support.

- `budget_status()`
  - Returns current LLM budget/spend status.

- `verify_signature(signature, payload)`
  - Validates the GitHub webhook HMAC signature.

- `webhook(request, x_hub_signature_256)`
  - Main webhook entrypoint.
  - Parses PR events or comment commands.
  - Decides whether to run.
  - Resolves preview URL.
  - Starts the background pipeline job.

- `_parse_glimpse_command(comment_body, command_prefix)`
  - Parses comment commands like `/glimpse --force --route /settings`.

- `_count_patch_changed_lines(patch)`
  - Counts changed lines in a patch for smart-trigger logic.

- `_is_ui_path(path, include_prefixes, exclude_substrings)`
  - Determines whether a changed file looks UI-relevant.

- `_resolve_preview_url_for_route(preview_url, start_route)`
  - Appends a route to the resolved preview URL.

## `app/steps/pipeline.py`

- `analyze_pr(repo_full_name, pr_number, pr_title, staging_url, ...)`
  - Planning phase orchestrator.
  - Fetches diff.
  - Extracts contract.
  - Evaluates trigger.
  - Calls step generation.
  - Returns planned steps and context.

- `run_pipeline(pr_number, preview_url, steps, ...)`
  - Execution phase orchestrator.
  - Runs capture.
  - Enforces sendable-video checks.
  - Renders approved frames.
  - Uploads final video.
  - Writes run metrics.

## `app/steps/step_generation.py`

- `_call_llm(client, model, messages, max_completion_tokens, response_schema=...)`
  - Calls the LLM with schema-constrained JSON output.
  - Falls back to JSON-object mode if needed.

- `_run_extraction_phase(client, model, diff_text, pr_title, contract, max_tokens)`
  - First LLM pass.
  - Extracts `start_route`, `terminal_testid`, `click_labels`, and `interaction_hints`.

- `_fallback_narration(pr_title)`
  - Builds simple narration when planning falls back.

- `_label_to_selector(label)`
  - Converts a label into a simple `data-testid`-style selector guess.

- `_extract_changed_testids_from_diff(diff_files)`
  - Pulls new `data-testid`s from added diff lines.

- `_start_route_candidates(start_route, extraction, real_routes)`
  - Builds prioritized possible start routes.

- `_upgrade_contract_from_extraction(contract, extraction)`
  - Merges extracted click labels back into the contract.

- `_contract_confidence(contract)`
  - Returns contract confidence (`high`, `medium`, `low`).

- `_can_attempt_direct_plan(contract)`
  - Decides whether the contract is strong enough for direct planning.

- `_should_fallback_to_guarded_screenshot(contract)`
  - Decides whether to enter safer discovery mode instead of full planning.

- `_log_click_stage(stage, steps)`
  - Logs click steps at different planning stages.

- `_route_snapshot_catalog(dom_data, fallback_routes)`
  - Builds route-by-route UI context for the planner.

- `_find_link_target_for_click(step, route_dom)`
  - Tries to infer navigation target from a click step.

- `_validate_against_route_snapshots(steps, dom_data, diff_files, ...)`
  - Checks planned steps against route-level DOM snapshots.

- `_synthesize_click_steps(extraction, contract, start_route)`
  - Builds fallback click steps from extracted labels if the plan degenerates.

- `_ensure_screenshots_for_visited_pages(steps)`
  - Automatically inserts screenshot steps after meaningful actions.

- `_inject_terminal_assertion(steps, contract)`
  - Adds an `assert_terminal` step if needed.

- `_inject_click_validation_from_terminal(steps, contract)`
  - Adds validation conditions to the last click using the contract terminal.

- `_inject_sequential_click_validations(steps)`
  - Adds proof conditions to click steps based on expected next state.

- `_build_planning_prompt(pr_title, extraction, real_routes, route_catalog, ...)`
  - Builds the main planner prompt with diff + DOM context.

- `generate_steps_from_diff(diff_files, pr_title, staging_url, ...)`
  - Main planner.
  - Crawls DOM.
  - Runs extraction and planning LLM calls.
  - Grounds, normalizes, and preflights the plan.
  - Returns steps and generation context.

## `app/steps/preflight.py`

- `_parse_interaction_hints(contract)`
  - Extracts prerequisite interaction hints from the contract notes.

- `preflight_gate(steps, contract)`
  - Checks whether the plan is valid before execution.
  - Verifies start route, required clicks, terminal assertion, and proof conditions.

## `app/steps/step_execution.py`

- `_resolve_browser_backend()`
  - Picks execution backend (`agent_browser_cli` by default or `playwright`).

- `_normalize_success_condition(raw)`
  - Converts raw proof conditions into typed validation objects.

- `_attach_test_case_success_conditions(steps, test_case_id)`
  - Adds inherited validation conditions from benchmark test cases.

- `_lookup_benchmark_result(experiment_summary, mode, test_case_id)`
  - Pulls benchmark results for experiment mode runs.

- `_collect_target_markers(generation_context)`
  - Collects changed testids / labels / terminal markers for sendability checks.

- `_result_path(result)`
  - Extracts resulting path from an execution result.

- `_result_mentions_marker(result, marker)`
  - Checks whether an execution result shows a target marker.

- `_build_render_approval(generation_context, results, approved_frames)`
  - Decides whether the capture is sendable.

- `run_capture(preview_url, steps, ...)`
  - Executes the planned steps.
  - Chooses backend.
  - Computes render approval.
  - Returns structured capture summary.

## `app/execution/step_runner.py`

- `_build_metrics(results, total_initial_steps, total_retries)`
  - Computes execution quality metrics.

- `_classify_final_outcome(success, failure_reason=...)`
  - Converts runner result into `passed`, `regressed`, `ambiguous`, or `inconclusive`.

- `_resolve_url(base, path)`
  - Resolves relative route paths to full URLs.

- `_extract_validation_condition(step)`
  - Pulls proof condition from a step.

- `_configure_ab_session(cli, capture_settings)`
  - Sets Agent Browser viewport/session config.

- `_settle_ab_page(cli, validation_condition=None)`
  - Waits for Agent Browser page to settle after navigation/clicks.

- `_resolve_ab_ref_with_commands(cli, intent, selector=...)`
  - Tries direct Agent Browser commands to find a target ref.

- `_scroll_to_find(cli, intent, selector=...)`
  - Scrolls and retries target finding.

- `_passes_preclick_safety_check(step, snapshot, chosen_ref)`
  - Blocks risky clicks if the resolved target does not match expectations.

- `_resolve_ab_click_target(cli, intent, snapshot, mode, ...)`
  - Main Agent Browser target resolution logic.

- `_ensure_ab_target_actionable(cli, click_target)`
  - Verifies target is visible and enabled before clicking.

- `_capture_ab_screenshot(cli, screenshot_dir, shot_idx, ...)`
  - Takes Agent Browser screenshots before/after actions.

- `_run_ab_click_attempt(...)`
  - Full Agent Browser click attempt:
  - snapshot before
  - resolve target
  - click
  - snapshot after
  - validate result

- `_ab_snapshot_to_dom_context(snapshot)`
  - Converts Agent Browser snapshot into DOM context for retries.

- `_objective_changed_testids(objective)`
  - Extracts changed testids from planning context.

- `_objective_start_route(objective)`
  - Extracts preferred start route from planning context.

- `_snapshot_contains_testid(snapshot, testid)`
  - Checks whether a target testid is visible in the snapshot.

- `_make_search_validation(value)`
  - Builds an `element_present` proof condition for changed-testid search.

- `_append_search_screenshot_result(...)`
  - Stores a search-mode screenshot result.

- `_should_use_testid_search(objective, initial_steps)`
  - Decides whether to enter changed-testid discovery mode.

- `_run_ab_changed_testid_search(...)`
  - Recovery/discovery mode that hunts for changed UI targets directly.

- `_next_click_intent(steps, start_index)`
  - Finds the next click’s semantic intent.

- `_snapshot_has_intent(snapshot, intent, mode)`
  - Checks whether a likely next target is currently visible.

- `_recover_ab_prerequisite_steps(...)`
  - Uses retry logic to recover missing earlier interactions.

- `_validated_milestone_steps(results)`
  - Pulls successful goto/click steps for replay.

- `_replay_ab_milestones(cli, preview_url, steps, mode, capture_settings)`
  - Replays milestones after recovery/restart.

- `_infer_runtime_validation(step, current_snapshot)`
  - Infers a runtime proof condition when one is missing.

- `_collect_ab_failure_diagnostics(cli)`
  - Captures console/page/network diagnostics on failure.

- `_attach_ab_failure_diagnostics(cli, step_result)`
  - Adds failure diagnostics to the step result.

- `_matches_validation_condition(condition, current_url, snapshot_text, element_names)`
  - Evaluates proof conditions against current state.

- `_describe_validation_actual(condition, snapshot)`
  - Describes what the validator actually observed.

- `_evaluate_click_validation(step, snap_before, snap_after)`
  - Core runtime proof check for clicks.

- `_validation_from_successful_text_wait(condition, current_url, snapshot_text, element_names)`
  - Shortcut validation when a text wait already proved success.

- `_is_stale_ref_error(error_message, click_target)`
  - Detects stale Agent Browser ref failures.

- `_approved_frame_paths(results)`
  - Selects which screenshots are safe to render into the final video.

- `_assert_ab_terminal_condition(cli, condition, expected_element, extract_snapshot)`
  - Validates the final terminal success state.

- `_execute_one(page, base_url, step, out_dir, shot_idx, ...)`
  - Runs one Playwright step.

- `run_stepwise(preview_url, initial_steps, objective, screenshot_dir, ...)`
  - Playwright-based stepwise executor.

- `run_ab_stepwise(preview_url, initial_steps, screenshot_dir, ...)`
  - Agent Browser-based stepwise executor and main runtime backend.

## `app/steps/dom_crawler.py`

- `_is_auth_wall(url)`
  - Detects auth/login redirects.

- `_discover_routes(page, staging_url)`
  - Finds routes from visible links.

- `_build_visit_order(seed_routes, discovered, max_routes)`
  - Chooses which routes to crawl first.

- `_short_selector(meta, fallback_tag)`
  - Builds short semantic selectors from extracted element metadata.

- `_extract_ui_from_current_page(page)`
  - Extracts buttons, links, inputs, and testids from a page.

- `_collect_ui_elements(page, url)`
  - Visits a URL and collects its UI elements.

- `_merge_snapshots(route_snapshots)`
  - Merges route-level DOM snapshots into one global snapshot.

- `crawl_dom_data(staging_url, seed_routes=None, max_routes=6)`
  - Main Playwright DOM crawler used by planning.

- `crawl_ab_routes(base_url, routes, session="ab_crawl")`
  - Agent Browser route crawler for experiments/debugging.

## `app/policy/selector_validator.py`

- `_known_button_texts(dom_ctx)`
  - Collects visible button/link labels from the DOM.

- `_allowed_raw_css(selector, dom_ctx)`
  - Determines whether a raw CSS selector is acceptable.

- `_selector_count_on_page(page, selector)`
  - Counts selector matches on the live page.

- `validate_step_against_dom(step, dom_ctx, page=None)`
  - Main selector safety check.
  - Rejects weak or invented routes/targets.

## `app/browser/ref_selector.py`

- `_make_candidate(element, match_type)`
  - Converts a UI element into a match candidate record.

- `_filter_by_role(elements, role_filter)`
  - Filters elements by role before matching.

- `_log_result(result)`
  - Logs selection decisions.

- `_slug_to_intent(slug)`
  - Converts selector-like text into a human-readable intent.

- `_candidate_texts(element)`
  - Extracts possible text signals from an element.

- `derive_intent(step)`
  - Turns a click step into a semantic intent like `Settings`.

- `select_ref(intent, snapshot, ...)`
  - Chooses the best Agent Browser ref for a given intent.

## `app/render.py`

- `render_video(approved_frames=None, render_approval=None)`
  - Renders approved screenshots into `out.mp4` using ffmpeg.

## `app/storage.py`

- `get_r2_client()`
  - Creates Cloudflare R2 client.

- `get_file_size_mb(file_path)`
  - Computes file size in MB.

- `list_videos(s3_client, bucket_name, prefix="videos/")`
  - Lists uploaded MP4s in storage.

- `cleanup_old_videos(max_videos=50, max_age_days=30)`
  - Deletes older/excess uploaded videos.

- `check_storage_usage()`
  - Returns storage count and size usage.

- `upload_video(local_path, auto_cleanup=True, pr_number=None)`
  - Uploads the final MP4 to R2 and returns public URL.

## `app/github_comment.py`

- `comment_on_pr(repo_full_name, pr_number, video_url=None, error_message=None, extra_note=None)`
  - Posts success or failure comment back to the PR.

## Memory Map

- `webhook.py` = start the run
- `pipeline.py` = orchestrate planning + execution
- `step_generation.py` = turn diff + DOM into steps
- `preflight.py` = check the plan
- `step_execution.py` = run capture + decide if publishable
- `step_runner.py` = execute + validate
- `dom_crawler.py` = inspect the real preview UI
- `selector_validator.py` = reject brittle selectors
- `ref_selector.py` = pick the right UI target
- `render.py` = frames to MP4
- `storage.py` = upload the MP4
- `github_comment.py` = report back to GitHub
