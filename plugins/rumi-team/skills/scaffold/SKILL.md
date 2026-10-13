---
name: scaffold
description: Set up a ready-to-work development environment for a project: a sensible folder structure, a working run and test setup, a .gitignore, a short README and a CLAUDE.md tailored to this specific project. Use when the user runs /rumi-team:scaffold, asks to "set up a new project", "create a dev environment", "make a CLAUDE.md for this project", or when Rumi's plan starts with the project setup requirement (R-0).
argument-hint: "[what the project is]"
---

Set up a development environment that suits **this** project. Many users aren't technical: explain choices in plain words, keep it small, and don't add tools they don't need.

## 1. Understand the project

- **If Rumi sent you,** the signed brief is your input: the requirement, interpretation, definition of done and `stack`. Don't ask the user anything else.
- **Otherwise,** use `$ARGUMENTS`, and look at what's already in the folder. If the purpose or the audience is unclear, ask at most two short questions with AskUserQuestion. For example: "What are you making?" (website, app, script or automation, API, data analysis) and "Who will use it?" Don't ask about programming languages unless the user brings one up.

## 2. Choose the stack (simplest thing that works)

Pick the smallest mainstream setup that fits, and say why in one sentence:
- **A simple web page or site:** plain HTML, CSS and JS. Add a dev server only if needed.
- **An interactive web app:** Vite with plain JS or React (only if the app is genuinely interactive) and Vitest.
- **An API or backend:** Node with Express and Vitest, or Python with FastAPI and pytest, whichever the folder already leans towards.
- **A script, automation or data work:** Python, with pytest, plus a `requirements.txt` or `pyproject.toml`.

If the folder already contains code, **keep its stack and conventions**. Only add what's missing.

## 3. Create the structure

- **Folders.** Create the folders the stack expects (for example `src/`, `tests/`, `public/` or `data/`). Put a one-line `README.md` in a folder only if its purpose isn't obvious.
- **Entry point.** Create a minimal entry point that runs, and one passing test, so "run" and "test" work from day one.
- **Supporting files.** Add a `.gitignore` for the stack. Always include `.env`, dependency folders and build output in it. Add a `.env.example` if the project will need settings or keys, with placeholder values only.
- **README.md.** What it is, how to run it, how to test it, in plain words.
- **Git.** If the folder isn't a git repo, run `git init`. Don't commit unless the user asks.
- **Approval for installs.** Installing dependencies needs the user's approval. Rumi asks through the Needs-you queue; on your own, ask before running the install.
- **Check it works.** Run the run and test commands once, and fix anything that fails.

**Never overwrite existing files.** If a file already exists (including CLAUDE.md), merge in the missing parts and keep everything the user wrote.

## 4. Write a tailored CLAUDE.md

CLAUDE.md is what every future Claude session reads first, so make it specific and true. Only include commands you actually ran successfully. Aim for under 100 lines, using these sections:

```markdown
# <Project name>

<One paragraph: what it is, who it's for, what problem it solves.>

## Commands
- Run: `<exact command>`
- Test: `<exact command>`
- <Lint / format / build, only if set up>

## Project structure
- `src/` – <what lives here>
- `tests/` – <what lives here>
- <every top-level folder, with one line each>

## How we build here
- Stack: <language, framework, test tool> – <one-line reason>
- Conventions: <naming, file layout, error handling, style rules that apply to this code>
- Tests: <where tests go, how they're named; every change comes with tests>
- Data: <where data lives; test data only in tests/fixtures; never use real personal data>
- Secrets: <settings go in .env (git-ignored); never read, print or commit .env>

## Definition of done
<From the signed brief if there is one; otherwise sensible defaults: tests pass, README updated, no secrets committed.>

## Working with Rumi's agent team
This project is built with the rumi-team plugin. Requirements are cards (R-1, R-2, …) on the Agent Team Board. Agents change only what their card needs, use test data, and stop to ask (through Needs you) before installing packages, touching data or contacting external services.
```

## 5. Report back

- **Working with Rumi:** report in the developer report format, with FILES CHANGED listing everything you created.
- **On your own:** tell the user, in a few short lines, what you set up, the commands to run and test it, and that CLAUDE.md now describes the project for future sessions.
