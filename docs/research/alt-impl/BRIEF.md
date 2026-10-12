<!-- markdownlint-disable MD013 MD060 -->

# Exploratory alternative implementation: shared brief for every work package

Status: speculative. Nothing here is adopted. The purpose is to build the alternative far enough, in isolated
branches, that the project can decide how and when to adopt parts of it. Read this first, then your own package.

## What is being explored

The design decisions are in `docs/adr/0008-0010` and `CONTEXT.md` (Degree, Rebuilt store, Temporal Edge). Evidence is
in `docs/research/spikes/functional-index-adjacency/` (README, RISKS, DEG-AUDIT). In one paragraph: the derived
`^KG` stores are written by about ten classes and go stale; the alternative gives them one owner and, later, has the
engine maintain them from the edge table; stored `deg`/`degp` go away; temporal edges own a structural row; `^NKG` and
other rebuilt stores record the stamp they were built from; the native-handle code is one module (`iris_vector_graph/host.py`,
already written, read it and `tests/unit/test_host.py`).

## Rules for every package

1. **Test first.** Write the failing test before the code that makes it pass, and say in your report which tests failed
   first. Never weaken or delete an existing test to make something pass; if a test asserts behaviour this design
   deliberately changes, change that test, say so in the report and explain why.
2. **Work only in your worktree and branch.** You are in an isolated git worktree. Commit on the branch you were
   given. Never push, never touch another remote, never create a PR or an issue, never tag, never edit `main`.
   No AI attribution in commit messages. Commit messages describe the change in plain words.
3. **Stay inside your package's files.** Other packages are being built in parallel by other agents on other branches
   and the results are merged later, so an edit outside your files is a merge conflict. If you need something outside
   them, write it in your report under "needs from the lead" and do not make the edit.
4. **Do not use the IRIS container.** No `docker`, no `iris session`, no connecting to ports 31972/21972/1972. Several
   agents run at once and a shared database would be corrupted. Tests you write that need a live IRIS are marked
   `@pytest.mark.e2e` (or live in `tests/e2e/`) and are NOT run by you; the lead runs them in one serial pass. ObjectScript
   cannot be compiled without the container, so write it carefully, follow `.claude/` and the style of the neighbouring
   classes, and mark it "uncompiled" in your report.
5. **Run the unit tests this way** (the worktree has no virtualenv; use the main checkout's, with the worktree's sources first):
   `PYTHONPATH=$PWD /Users/tdyar/ws/iris-vector-graph/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider`
   Check once that `PYTHONPATH=$PWD /Users/tdyar/ws/iris-vector-graph/.venv/bin/python -c "import iris_vector_graph; print(iris_vector_graph.__file__)"`
   prints a path inside your worktree. Run the unit tests that touch the files you changed (use `-k` or paths), and
   `tests/unit/test_host.py`. Do not run the whole suite; it takes several minutes. Report what you ran.
6. **ObjectScript conventions** (from the repo's CLAUDE.md): `Set tSC = $$$OK` first and `Quit tSC` last in methods that
   return a status; use `$$$OK`/`$$$ISERR()`/`$$$ERROR()`; never `$GET` on an object property; no `ORDER BY` with
   `SELECT TOP`; operators evaluate strictly left to right with NO precedence, so parenthesise every compound
   condition (`(a = "") || (b = "")`); the graph key is always the first subscript after the store name (ADR-0001);
   the default graph's key is `0` (`Graph.KG.GraphKey.ForIndex`). Postconditionals take no spaces.
7. **Markdown**: after writing or editing a `.md` file run `markdownlint-cli2 --fix <file>` then `prettier --write <file>`.
8. **Do not invent APIs.** Read the code you change and the code it calls. When something you need does not exist,
   say so in the report.

## Report format (your final message)

```text
PACKAGE: <name>   BRANCH: <branch>   COMMITS: <hashes>
DONE: <what works, in a few lines>
TESTS: written first: <names>; ran: <command and result>; not run (need IRIS): <names>
FILES: <changed files>
UNCOMPILED OBJECTSCRIPT: <files>
DEVIATIONS: <anything you did differently from the package text, and why>
NEEDS FROM THE LEAD: <edits outside your files, decisions, live checks>
RISKS: <what could be wrong that you could not check>
```
