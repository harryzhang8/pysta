# Contributing to pysta

Thanks for taking a look. This is a teaching project, so the bar for "is this
worth merging" is a bit different from a normal library.

## Guiding principles

1. **Zero runtime dependencies.** Standard library only. If a change needs
   `numpy`, `networkx` or `lark`, it belongs in a fork.
2. **Exact arithmetic.** Times are `fractions.Fraction`. Never introduce a
   `float` into the analysis path — `float` is only allowed at the very last
   step, when formatting a number for display.
3. **Readable over clever.** The engine exists so someone can read it in an
   afternoon. Prefer an explicit loop you can step through over a folded
   comprehension.
4. **Every claim is checked.** If you add behaviour, add an expectation to
   `src/pysta/checks.py` so `pysta check` covers it.

## Getting set up

```bash
git clone https://github.com/harryzhang8/pysta
cd pysta
pip install -e ".[dev]"

pytest          # 137 tests
pysta check     # 32 expectation checks, exits non-zero on any failure
```

Both must pass before you open a pull request — that is exactly what CI runs
(3 operating systems × Python 3.9 / 3.12 / 3.13).

### Optional: real synthesis and simulation

`tools/rtl_check.py` runs `iverilog` and `yosys` over every RTL example, which
proves the examples are actually synthesizable:

```bash
python tools/fetch_oss_cad_suite.py   # ~570 MB, portable, no installer
python tools/rtl_check.py             # 11/11 pass
```

> **Windows gotcha:** `yosys` ships an `abc` binary that fails on non-ASCII
> paths (`init_share_dirname: unable to determine share/ directory`). The fetch
> script defaults to `%LOCALAPPDATA%\pysta-tools\`, and `rtl_check.py` stages
> sources into an ASCII temp directory before running.

## Adding an example

Each example lives in three places and they must agree:

| File | Role |
|---|---|
| `src/pysta/examples/rtl/<name>.v` | behavioural intent, human-readable |
| `src/pysta/examples/netlist/<name>.v` | gate-level netlist — what STA actually reads |
| `src/pysta/examples/sdc/<name>.sdc` | constraints, **heavily commented** |

Then register it in `EXAMPLES` in `src/pysta/checks.py` with at least one
`Check`, and pick the **exact** expected value — a `Fraction` when the value is
not a terminating decimal. `4/3 * 3` is `4`; write it as `Fraction(4)` and let
the engine prove it.

Good candidates for new examples are listed in the README's "Contributing"
section — `create_generated_clock`, `set_case_analysis`, `set_disable_timing`,
output-port min delay, and divided clocks are all still missing.

## Fixing a parser bug

Parsers are where the interesting bugs live. When you fix one, please:

* add a minimal netlist/SDC/lib snippet that reproduces it,
* add a test in the matching `tests/test_<module>.py`,
* and keep the fix narrow — a parser that "helpfully" guesses tends to break
  the next thing.

## Style

* No formatter is enforced; just match the surrounding code.
* Comments and docstrings are currently in Chinese, which is the author's
  native language. **Translating them to English is a genuinely welcome PR** —
  do it in focused batches, not one giant diff.
* Keep the public API surface small. `pysta.__init__` re-exports the things a
  user needs; everything else is internal.

## Reporting a bug

Please include:

* the netlist / SDC / `.lib` snippet (or the example name),
* what `pysta` printed,
* what you expected and, if you have one, a reference value from another tool.

## License

By contributing you agree that your work is licensed under the
[Apache License 2.0](LICENSE).
