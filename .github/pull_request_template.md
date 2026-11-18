## What and why

<!-- What changes for users, and why. Technical only: this repository is public. -->

## How to test

<!-- Commands, pages, what to look for. -->

## Before merging

<!-- Setup this needs outside the code (AWS / terraform apply, GitHub variables or secrets, DNS,
external accounts, data), each with who does it. Write "Nothing" if there is none. -->

- [ ] Safe to deploy as soon as it's merged: it needs no setup, or it's switched off until the setup above is done
- [ ] `uv run pytest` passes; new behaviour has tests
- [ ] New UI text is in all four languages, with formal address
- [ ] Merge order: <!-- "No dependency", or "after #n" / "before #n" -->
- [ ] No business details (strategy, fees, partners, investors) in the code, commits or this description

## After merging

<!-- Anything to do once it's live (turn a switch on, check a page, tell testers). "Nothing" if none. -->
