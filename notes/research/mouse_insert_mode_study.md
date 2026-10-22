The initial investigation found several input-state problems. The later exact reproduction—startup dashboard, Explorer, open a file, move to row 8, then press `i`—identified a stale dashboard shortcut as the cause of that sequence. That dashboard issue was fixed separately.

The remaining confirmed problems are now covered by [mouse input regression tests](../../tests/test_mouse_input_routing.py):

- A text click releases sidebar and bottom-panel keyboard focus without hiding the panels.
- A text click clears unfinished commands, counts, and register prefixes. A pending `r` can no longer turn the next `i` into an unintended replacement.
- Opening a different document or returning to another window clears unfinished normal-mode commands. Ordinary multi-key commands in the same view still work.
- A fresh click ends an old visual selection. A subsequent drag begins a new selection at the clicked position.
- A text click dismisses command entry and substitute confirmation without executing the command or accepting another replacement.
- Insert and replace mode survive text clicks. Moving an insert session between documents closes the previous document's undo group and starts a separate group in the clicked document.
- Switching between sidebar and bottom-panel content by mouse releases the other host's keyboard focus.
- Left-button presses cancel active Flash searches and target choosers. Wheel events and button release do not cancel them.
- Clearing a pending prefix by mouse also emits the completion event that dismisses which-key.

Flash already cancels immediately when its Escape handler receives `<Esc>`. Tests exercise every phase: waiting for the first character, waiting for the second character, selecting a label, and selecting the second character of a long label. The following key reaches the editor. Cancellation from visual mode deliberately restores the original selection; the next key then has its usual visual-mode meaning. The mouse fix addresses starting Flash and then clicking elsewhere without first pressing Escape.

The old exploratory reproduction script asserted the previous faulty behavior. It has been retired in favor of regression tests that assert the intended behavior, including controls for scrolling, scrollbar interaction, genuine Alt bindings, panel navigation, drag selection, dashboard navigation, and normal multi-key editing.

A separate terminal-protocol limitation remains: the prompt-toolkit backend treats Escape and a printable character received in one input batch as an Alt combination. On terminals that encode both the same way, rapid Escape–`i` can arrive as `<A-i>`. This is not evidence that Flash consumed a delivered Escape, and blindly changing Alt+i into `i` would break intentional bindings. The existing backend Alt-translation tests remain intact. Mouse cancellation does not strip a genuine subsequent Alt key.

The tests use the real event loop, editor API, modal engine, mouse dispatcher, and Flash plugin with finite headless input. They verify routing and document changes, rather than physical terminal timing or window-manager behavior.
