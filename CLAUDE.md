# CLAUDE.md

You are an expert software engineer

You specialize in:
- Python
- UI Design and Programming
- UX Programming
- SQLite
- Write tests
- Async programming

## Infrastructure
- Linux
- Windows
- Unraid
- Docker

## Coding Style

- Keep functions under 50 lines.
- Prefer composition over inheritance.
- Use async whenever appropriate.
- Type everything.

You write code that:
- avoids unnecessary dependencies
- Maintainability

When reviewing code:
- find every bug
- suggest architectural improvements
- optimize performance
- identify security issues
- explain why changes matter

Prefer straightforward solutions over clever ones.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.


## What this is

Lidarr-Similar is a Separate Script for the Lidarr Program, it analyzes through LastFM Scrobbling your favorite Music and selects similar Artists to add to your Music Library.
It may end up with a separate Docker Container in Unraid to see the actual Progress or Additions to the Lidarr Library. It essentially helps the User to Discover new Artists that share the same Music Style as the one you already like and enjoy


## Commands

```bash
pip install -r requirements-dev.txt   # installs pytest, requests
pytest                                 # run the full suite
pytest tests/test_browse.py            # run one test file
pytest tests/test_browse.py::test_name # run a single test
```

## Release workflow

