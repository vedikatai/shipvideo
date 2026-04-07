LAUDYA

Yes. We are using Agent Browser, but not leveraging it enough.

Right now our wrapper mainly uses:

- `open`
- `snapshot`
- `click`
- `wait --load`
- `wait --text`
- `wait --url`
- `screenshot`
- `console`
- `errors`
- `network requests`
- `find testid/role/label/text`

That is only part of what Agent Browser gives us.

From the Agent Browser docs/repo, the useful primitives we are underusing are:

- Annotated screenshots: `agent-browser screenshot --annotate`, useful for debugging and for LLM/agent step repair. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- Snapshot diffs: `agent-browser diff snapshot`, useful for proving what changed after a click. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- Screenshot diffs: `agent-browser diff screenshot`, useful for visual proof and regression detection. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- `get box`: bounding box for real position-aware fallback. Our current “position fallback” is only snapshot order, not geometry. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- `wait --fn`: wait for exact app-specific JS/DOM conditions instead of generic `networkidle`. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- `batch`: execute multiple commands in one process to reduce process overhead and avoid flaky command-to-command gaps. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- `trace start/stop`: capture real debug traces for failed flows. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- HAR/network recording: useful when proof depends on API completion. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- State/session persistence: important for authenticated app flows. Source: [Agent Browser README](https://github.com/vercel-labs/agent-browser)
- Skills: the `agent-browser` skill teaches the navigate → snapshot → interact → re-snapshot workflow, and `dogfood` is a structured exploratory testing workflow with screenshots/repro videos. Source: [Agent Browser skills](https://agent-browser.dev/skills)

The biggest miss in our current architecture is this:

```text
click -> compare our normalized snapshots -> infer proof
```

We should make Agent Browser itself produce stronger evidence:

```text
before snapshot
before screenshot
click
wait for explicit proof
after snapshot
after screenshot
snapshot diff
optional screenshot diff
console/errors/network
trace on failure
```

**What I would change next**

1. Add `annotated_screenshot(path)` to [`app/browser/agent_browser_cli.py`](/Users/sourabhligade/shipvideo-engine/app/browser/agent_browser_cli.py).

2. Add `get_box(ref_or_selector)` so our position fallback uses real geometry, not just snapshot order.

3. Add `wait_for_function(js_expr)` and use it for proof conditions like:
```js
document.body.innerText.includes("Recharge Successful")
```

4. Add `diff_snapshot()` after every click and store it in the step result.

5. On failed proof, automatically capture:
- annotated screenshot
- raw snapshot
- snapshot diff
- console messages
- page errors
- network requests
- optional trace

6. Use `batch` for deterministic runs once the flow is known, to reduce per-command process overhead.

7. Do not use `agent-browser chat` in the production path. That would recreate the same “agent guesses flow” problem. Use Agent Browser’s deterministic primitives and evidence collection instead.

**Bottom Line**

Agent Browser is enough for our MVP browser layer. We probably do **not** need Stagehand yet.

Our issue is not that Agent Browser lacks capability. It is that we are using it mostly as a basic click/screenshot wrapper instead of as a full evidence engine.

The best next move is to upgrade our Agent Browser wrapper and step runner around proof:

```text
manifest step -> Agent Browser action -> explicit wait/proof -> diff/screenshot evidence -> pass/fail
```

That gets us closer to the 19/20 reliability target than adding another automation framework right now.