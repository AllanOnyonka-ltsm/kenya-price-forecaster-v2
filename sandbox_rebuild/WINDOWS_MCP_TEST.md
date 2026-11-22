# Windows-MCP typing test (VS Code), 2026-11-22

Tool under test: Windows-MCP (CursorTouch, `windows-mcp` 0.8.5, user scope, telemetry off).
Target: a new VS Code window opened with `code -n` on `sandbox_rebuild\` only, editing `sandbox_rebuild\typing_test.py`.
No other window was typed into, and nothing outside `sandbox_rebuild\` was changed. `git status` at the end matched the start of the session.

## Tool list

| Tool | What it does |
|---|---|
| `App` | Launches an app by Start-menu name or exe path, resizes a window, or switches focus to one (`mode=switch`). |
| `Click` | Left, right or middle click (or hover) at coordinates or at a UI-element label from Snapshot. |
| `Clipboard` | Gets or sets clipboard text. |
| `DisplayInventory` | Lists monitors with their bounds, DPI and scale. |
| `FileSystem` | Reads, writes, copies, moves, deletes, lists, searches and gets info on files. |
| `Move` | Moves the mouse or drags. |
| `MultiEdit` | Types into several fields in one call. |
| `MultiSelect` | Clicks several items, optionally holding Ctrl. |
| `Notification` | Shows a Windows toast. |
| `PowerShell` | Runs a PowerShell command. |
| `Process` | Lists or kills processes. |
| `Registry` | Gets, sets, deletes or lists registry values. |
| `Scrape` | Fetches a web page, or the DOM of the active browser tab. |
| `Screenshot` | Fast screenshot with no UI tree. Can capture one display. |
| `Scroll` | Scrolls at a location or element. |
| `Shortcut` | Sends a key chord such as `ctrl+s`. |
| `Snapshot` | Screenshot plus the accessibility tree: focused window, open windows, element coordinates. |
| `Type` | Clicks a location or element, then types text. Options: `clear`, `press_enter`, `caret_position`. It has no typing-speed setting, so a "human-ish pace" isn't possible. |
| `Wait` | Sleeps for N seconds. |
| `WaitFor` | Polls the accessibility tree until a condition holds, e.g. `active_window` matches a title. I used it as the focus guard before every keystroke. |

## Method

1. Created `.vscode\settings.json` with the settings from the brief, plus an empty `typing_test.py`.
2. Ran `code -n sandbox_rebuild`, then `WaitFor active_window "sandbox_rebuild"`, which matched in 6.3 s. Ran `code -r typing_test.py`.
3. Took a Snapshot before typing. The focused window was `typing_test.py - sandbox_rebuild - Visual Studio Code` (handle 69448).
4. For each run:
   - `WaitFor active_window "typing_test.py - sandbox_rebuild"` (focus guard)
   - `Shortcut ctrl+a`, then `Shortcut delete`
   - `Type` with `loc` set to an empty point in the editor, all 11 lines in one call
   - focus guard again
   - `Shortcut ctrl+s`
5. Verified each run in PowerShell: an exact byte comparison against the intended text, a comparison after CRLF→LF normalisation, a per-line diff, and `python typing_test.py`.

## Results

Runs 1–3 used the settings exactly as given in the brief. Runs 4–5 were extra diagnostic runs, made after the cause was found (see below).

| Run | Settings | Result | Diff vs intended | `python` output | Time* | Focus problems |
|---|---|---|---|---|---|---|
| 1 | brief | **FAIL** | L5, L7, L11 had 8 spaces instead of 4 (+12 bytes) | `IndentationError` L5 | 40.6 s | none |
| 2 | brief | **FAIL** | L3, L4, L5, L6, L7, L11 had 8 spaces instead of 4 (+24 bytes) | `IndentationError` L3 | 38.7 s | none |
| 3 | brief | **PASS** | identical after CRLF→LF (10 CRLFs, 430 vs 420 bytes) | `263.7` | 33.4 s | none |
| 4 | brief + `[python]` override | **PASS** | identical after CRLF→LF | `263.7` | 31.7 s | none |
| 5 | brief + `[python]` override | **PASS** | identical after CRLF→LF | `263.7` | 32.1 s | none |

\*Time runs from the start timestamp to the end of verification. It includes the focus guards (each `WaitFor` took about 3.5 s even when focus was already correct), the clear, the Type call, the save and the diff.

Every Type call landed in the sandbox editor. The `Type` result echoed the exact intended text each time. No stray characters were added, none were dropped, and nothing was reordered. The only changes on disk were leading whitespace and line endings.

Line endings: VS Code saved CRLF because the file was created empty on Windows and `files.eol` defaults to `auto`. Adding `"files.eol": "\n"` later doesn't convert a file that's already open. So the exact-byte comparison fails in every run, while the content matches in runs 3–5. For byte-exact output, either create the file with LF content first or set `files.eol` before the file is first opened.

Distance: the script prints **263.7 km**. That is the correct great-circle distance from Nairobi CBD to Kisumu. The brief's "about 280 km" is a rough estimate (road distance is about 340 km), so this is not a typing error.

## Root cause of runs 1–2

Pylance (`ms-python.vscode-pylance-2026.4.1`) ships this as a default setting:

```json
"[python]": { "editor.formatOnType": true }
```

A language-specific setting overrides the general `editor.formatOnType: false` from the brief, so Pylance's on-type indent stayed on. It runs asynchronously as each newline arrives. Windows-MCP types fast, so sometimes the indent edit lands after the 4 typed spaces and adds 4 more. That's why the failing lines changed from run to run (L5/L7/L11 in run 1, L3–L7/L11 in run 2) and why run 3 passed by luck.

The fix, used in runs 4–5 and now saved in `sandbox_rebuild\.vscode\settings.json`:

```json
"[python]": { "editor.formatOnType": false, "editor.autoIndent": "none" },
"python.analysis.autoIndent": false,
"files.eol": "\n"
```

Two clean runs is a small sample. It fits the explanation, but it doesn't prove the race is gone.

## Comparison with winctl's overnight failures

| winctl failure | Windows-MCP in this test |
|---|---|
| Ctrl+P did not open Quick Open | `Shortcut ctrl+p` opened Quick Open on the first try. It was empty (no stray text) and inside the sandbox window. I closed it with Escape without typing. |
| A path was typed into the wrong buffer | 0 of 5 runs went to the wrong place. `WaitFor active_window` checks the title before each chord, and `Type` clicks its target coordinates first, so the caret is always placed in the intended editor. |
| (none) | **New risk:** typing is fast and has no speed control, so it can race editor features that act asynchronously (Pylance on-type formatting). Turn those off before typing code. |

Two other things to know:

- `Screenshot` (fast mode) reported "No active window found / No windows found" every time, even with VS Code focused. Use `Snapshot` or `WaitFor` for focus checks, not `Screenshot`'s text header.
- `Type` needs a `loc` or `label` and clicks there before typing. That's good for safety, but a wrong coordinate would move the caret. Point it at an empty area below the last line so the caret goes to the end of the file.

## Verdict

**Reliable for focus and keystroke delivery. Not reliable for Python code with this repo's VS Code settings as given in the brief.**

- Keystroke delivery was 5/5 to the correct window and 5/5 character-exact as sent, and Quick Open worked. These were winctl's two failure modes, and neither happened.
- The two failed runs were caused by the editor (Pylance's on-type indent overriding the general setting), not by Windows-MCP. With the `[python]` override, 2/2 runs were clean.
- Before redoing real project edits with it: add the `[python]` overrides above to whichever workspace is being typed into. Keep the `WaitFor active_window` guard before every chord. Verify each file on disk (normalise CRLF, then run the tests) after every save.

## Tab switching

Requested by the parent session at 09:34–09:39 on 2026-11-22. The `[python]` overrides were kept, and the user was hands off.

Setup:
- Three LF files `tab_a.py`, `tab_b.py` and `tab_c.py`, each containing `# file X\n`.
- Closed all editors in the sandbox window (Ctrl+K Ctrl+W, all saved), then `code -r tab_a.py tab_b.py tab_c.py`. Tab order was a, b, c.
- Got tab coordinates from a Snapshot (UI labels `tab item "tab_X.py"`): a (3206,237), b (3312,237), c (3418,237).

Each step followed this sequence:
1. Focus guard: `WaitFor active_window "sandbox_rebuild - Visual Studio Code"`.
2. Switch:
   - (i) `ctrl+p`, then `WaitFor text_exists "Search files by name"` (the empty Quick Open placeholder), then `Type "tab_X.py"` with `press_enter`.
   - (ii) `ctrl+pagedown` or `ctrl+pageup`, one press at a time, with a title check after each press.
   - (iii) a screenshot to make sure the tab strip is clear, then `Click` the tab coordinates.
3. Confirm: `WaitFor active_window "tab_X.py - sandbox_rebuild"`.
4. Type the marker: `ctrl+end`, then `Type "# step NN -> tab_X via <method>\n"` at an empty point in the editor, then `ctrl+s`.

### Per-step results

| Step | Method | Target | Title confirmed | Marker landed correctly | Time |
|---|---|---|---|---|---|
| 01 | Ctrl+P | tab_a | y | y | 25.8 s |
| 02 | Ctrl+PageDown ×2 (a→b→c) | tab_c | y (intermediate b also confirmed) | y | 22.0 s |
| 03 | click | tab_b | y | y | 19.4 s |
| 04 | Ctrl+P | tab_b (already active) | y, plus a screenshot to prove Quick Open had closed | y | 53.1 s† |
| 05 | Ctrl+PageDown ×2 (b→c→a, wraps) | tab_a | y (intermediate c also confirmed) | y | 22.4 s |
| 06 | click | tab_c | y (WaitFor took 12.3 s) | y | 23.7 s |
| 07 | Ctrl+P | tab_b | y | y | 23.3 s |
| 08 | Ctrl+PageUp ×1 (b→a) | tab_a | y | y | 16.7 s |
| 09 | click | tab_c | y | y | 18.9 s |
| 10 | Ctrl+P | tab_a | y | y | 23.0 s |
| 11 | Ctrl+PageDown ×1 (a→b) | tab_b | y | y | 16.2 s |
| 12 | click | tab_c | y (WaitFor took 12.0 s) | y | 22.0 s |

†Step 04 includes a 10 s pause while a HeyClicky popup covered the tab strip (see pitfalls).

Total time was 4 min 52 s for 12 steps, about 24 s per step. Most of it was the WaitFor guards, which never return in under about 3 s.

### Verification on disk

Each file was compared byte for byte against a rebuilt expected string (`# file X\n` plus that file's markers in step order, each ending in `\n`):

| File | Bytes | CR bytes | BOM | Exact match |
|---|---|---|---|---|
| tab_a.py (steps 01, 05, 08, 10) | 141 | 0 | no | **yes** |
| tab_b.py (steps 03, 04, 07, 11) | 135 | 0 | no | **yes** |
| tab_c.py (steps 02, 06, 09, 12) | 133 | 0 | no | **yes** |

Every marker is in the intended file and in order. There is no stray text anywhere, including no leftover Quick Open text and no accepted Copilot suggestion. `git status` outside `sandbox_rebuild\` was unchanged.

### Success rate per method

| Method | Switches | Title confirmed | Marker correct |
|---|---|---|---|
| (i) Ctrl+P + name + Enter | 4 | 4/4 | 4/4 |
| (ii) Ctrl+PageDown / PageUp | 4 (7 key presses) | 4/4 (7/7 including intermediate tabs) | 4/4 |
| (iii) Click tab | 4 | 4/4 | 4/4 |
| **Total** | **12** | **12/12** | **12/12** |

### Pitfalls hit

1. **A third-party popup covered the tabs.** During step 04, a HeyClicky notification card appeared over the top of the sandbox window. It covered the tab_b and tab_c tabs, so a click there would have hit the popup's "Full report" button instead. I paused without typing or clicking, and it dismissed itself after about 10 s. Before every click-based switch, take a screenshot. Don't trust stale coordinates.
2. **The mouse moved on its own.** The pointer moved (to 3747,108 and then 3675,484) while I was doing nothing, probably because of the HeyClicky overlay. Keyboard and focus were not affected. Still, nothing should assume the mouse is where the agent left it. `Type` and `Click` both pass explicit coordinates, which avoids the problem.
3. **Copilot next-edit suggestions.** After a few markers, Copilot began offering ghost text in other tabs ("`# step 05 -> tab_b via ctrl+page…`, Tab to open"), and the suggestion was wrong. Nothing was accepted because the test never sends Tab, but one stray Tab key would write a wrong line into a real file. Recommended sandbox and workspace settings: `"editor.inlineSuggest.enabled": false` and `"github.copilot.nextEditSuggestions.enabled": false`.
4. **The title check can't detect a switch to the file that's already active.** In step 04, the title was `tab_b.py…` both before and after Ctrl+P, so it couldn't show whether Quick Open had closed. If Quick Open were still open, the marker would have gone into it. A screenshot confirmed it had closed. For every Ctrl+P switch, also confirm the placeholder is gone, or switch to a different file first.
5. **WaitFor matches substrings.** `tab_b.py - sandbox_rebuild` also matches `● tab_b.py - …` (a file with unsaved edits). Here the files were always saved before switching, so it didn't matter. In real use, check for the `●` separately.
6. **WaitFor is slow.** It always takes at least about 3 s, even when the condition is already true, and twice took about 12 s. That doesn't affect correctness, but each guarded step costs about 20 s.
7. **Ctrl+Tab was not used.** In VS Code, Ctrl+Tab opens a most-recently-used picker while Ctrl is held. The `Shortcut` tool releases Ctrl straight away, so Ctrl+Tab only flips between the last two editors. Use Ctrl+PageDown/PageUp, whose order follows the tab strip, with a title check after each press.
8. **`Type` always clicks before typing.** The marker was typed with `loc` pointing at an empty part of the editor, which puts the caret at the end of the file, the same as Ctrl+End. If the click point ever lands on text, the caret moves there. For longer files, use Ctrl+End followed by a click below the last visible line, or check the caret position in the status bar.

### Verdict

**Safe for editing several real repo files per task, as long as these guards are kept.**

All 12 of 12 switches reached the intended tab, using all three methods, and every marker is byte-exact on disk. There were no wrong-buffer writes, and Quick Open never held stray text. Those were exactly winctl's overnight failures.

What made it safe was the guard discipline, not the tool on its own:
- a title check after every switch and before every keystroke
- an empty-placeholder check before typing into Quick Open
- a screenshot before any click
- saving before every switch, so a `●` can't hide a mistake
- verifying files on disk afterwards

For real repo files, also turn off Copilot inline and next-edit suggestions and keep the `[python]` overrides. Prefer Ctrl+P, which is deterministic and doesn't depend on tab order or coordinates, then Ctrl+PageDown/PageUp. Use clicks only when a fresh screenshot shows the tab strip is clear. The cost is speed: about 20–25 s per guarded switch-and-edit.
