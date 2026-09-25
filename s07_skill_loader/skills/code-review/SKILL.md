---
name: code-review
description: Review code against a fixed narrow scope - syntax, function calls, types, empty implementations
---

# Code Review

Review code with a fixed, narrow scope. Report only defects that would break at
runtime or mislead the reader about what the code does. Do not pad the report
with style or optimization suggestions.

## In scope — report these

1. **Syntax** — anything that does not parse, or parses but always raises.
2. **Function calls** — a called name that does not exist anywhere in the repo,
   wrong argument count, wrong keyword name, or a call site that disagrees with
   the callee's actual signature.
3. **Types** — a value that cannot be what the surrounding code assumes. Includes
   `None` used as a value, container element type mismatches, and passing an object
   where its `.content` (or similar) was meant.
4. **Empty implementations** — `pass`, `...`, `raise NotImplementedError`, a stub
   that returns a fixed placeholder, or a branch that silently does nothing where
   the caller expects an effect.

## Out of scope — do NOT report

- Style, naming, formatting, import ordering, unused imports.
- Performance, or "this could be written more simply".
- Missing tests, missing docs, missing type annotations.
- Anything you cannot tie to a concrete failure.

## Procedure

1. Read every file in scope end to end before judging any single line.
2. Before claiming a name is undefined, grep the whole repo for its definition.
3. Before claiming a call is wrong, read the callee's real signature.
4. Before claiming a type mismatch, trace where the value actually comes from.
5. Reproduce when you can:
   - `uv run python -m pyflakes <path>` for undefined names
   - `uv run python -c "import <module>"` for import-time failures
   - run the entry point for anything you claim is broken
6. Star imports make pyflakes report false positives — confirm by reading the
   source module before reporting.

## Output format

One block per finding, most severe first:

    <file>:<line>  <what breaks>
    evidence: <the command you ran, and its output>

End with a plain verdict. If nothing in scope was found, say exactly that.
