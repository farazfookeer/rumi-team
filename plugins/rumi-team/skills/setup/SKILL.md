---
name: setup
description: Create the user's own private Agent Team Board on claude.ai and connect Rumi's team to it. Use when the user runs /rumi-team:setup, says "set up the board" or "make me a new board", or when Rumi finds no board in ~/.claude/rumi-team/config.json.
---

Set up the user's **Agent Team Board**: a private live page on their claude.ai account where Rumi's team shows its work. Talk to the user in plain, friendly language. Many users aren't technical, so don't mention file paths or tool names unless something goes wrong.

The plugin root is the directory two levels above this skill's base directory (it contains `board/`, `agents/` and `scripts/`).

1. **Check for an existing board.** Read `~/.claude/rumi-team/config.json`. If it has a `boardUrl` and the user didn't ask for a new board, tell them they already have one and give them the link. If they asked about approval pop-ups, do step 6. Otherwise stop.

2. **Check that boards are available.** The board needs the `Artifact` and `ArtifactData` tools, which come with signing in to Claude Code with a claude.ai account. If either is missing, even after a ToolSearch for it, tell the user plainly: "The live board needs you to be signed in to Claude Code with your claude.ai account. The team still works without it; you just won't see the live board." Write `{"boardUrl": null, "boardDisabled": true}` to the config file so Rumi doesn't ask again, and stop.

3. **Copy the page somewhere you can publish from.** Copy `<plugin root>/board/board.html` into your scratchpad directory if this session has one. Otherwise copy it into a temporary folder `.rumi-board-setup/` in the current working directory. Read the copy in full before publishing it.

4. **Publish it as a new, private artifact** with the Artifact tool:
   - `file_path`: the copy
   - `icon`: `"kanban"`
   - `description`: `"Live board for Rumi's agent team: requirements, handoffs, decisions, tokens and tool calls."`
   - `capabilities`: `{"db": {"rules": [{"path": "", "read": "view", "write": "admin"}]}}` (only the owner and editors can change the board)

   Then delete `.rumi-board-setup/` if you created it.

5. **Save the link.** Create `~/.claude/rumi-team/` if needed and write `config.json`:
   `{"boardUrl": "<the published URL>", "createdAt": "<ISO UTC time>"}`

6. **Offer fewer approval prompts.** Ask the user with AskUserQuestion: "Rumi updates your board at every step, and Claude Code normally asks you to approve each update. Allow board updates without asking?" Options: "Yes, allow board updates (Recommended)" or "No, ask me each time". If they say yes, add `"ArtifactData"` to `permissions.allow` in `~/.claude/settings.json`. Read the file first, keep everything else in it exactly as it is, and create it if it doesn't exist. Only make this change if they said yes.

7. **Finish** with a short message:
   - the board link, and that it's private to them (they can share it from the page's Share menu)
   - that it starts empty and fills in when they give Rumi something to build
   - how to start: type `/rumi-team:rumi` followed by what they want built
