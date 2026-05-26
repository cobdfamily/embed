# Comment style for embed

Same conventions as the other COBD microservices
(salmon, townsfolk, brian, dispatch).

## What to comment

**Comment the WHY, not the WHAT.** Names + types
already say what code does; what isn't obvious is
why we picked this shape over alternatives, what
invariant the code is preserving, or what real-
world incident motivated the line.

Good:

```python
# A 200 with no `html` field is treated as a
# miss -- some providers return error envelopes
# inside a 200.
if not isinstance(html, str) or not html.strip():
    return None
```

Bad:

```python
# Check if html is a non-empty string
if not isinstance(html, str) or not html.strip():
    return None
```

## What NOT to comment

- Type information that's already in the
  annotation.
- "Used by route X" or "added in commit Y" --
  belongs in the PR description / git blame, not
  the source.
- TODO markers without an owner + date.
- `# noqa: ...` without an inline explanation of
  why the rule is being silenced.

## Block comments at the top of each module

Every module starts with a docstring that
explains:

1. What the module is responsible for.
2. The deployment / runtime context that
   constrains the design.
3. Any non-obvious decisions made elsewhere in
   the module body so a reader can spot them.

See `src/embed/main.py` and `src/embed/oembed.py`
for the canonical shape.

## Inline-comment rules

- Keep lines under 100 chars (ruff's default).
  Multi-line wrap is fine when the rationale
  needs it.
- Avoid comments that paraphrase the next line.
  If the next line is hard to read, fix the
  line, not the comment.
- When you delete code, delete the comment that
  explained it. Stale comments are worse than no
  comments.

## Tests

Tests are documentation. Every test name should
be a sentence that says what behaviour it
guards. `test_csp_multiple_headers_intersect_to_refused`
is right; `test_csp_2` is wrong.

Add a short docstring inside the test ONLY when
the behaviour being tested is subtle (race,
edge case, regression for a specific bug). Don't
add a docstring that paraphrases the test name.
