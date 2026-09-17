---
name: Bug report
about: Something produces a wrong number, or crashes
title: ''
labels: bug
assignees: ''
---

<!-- Please paste the actual files you ran, not a paraphrase. -->

## Reproduce

```bash
# the exact command, e.g.
python -m pysta report ex9
```

## Inputs

Netlist / SDC / `.lib` snippet, or the bundled example name.

```verilog

```

```tcl

```

## What happened

```
paste the output here
```

## What you expected

Tell us the number you expected, and where it comes from — a hand calculation,
a textbook, or another STA tool.

## Environment

- `pysta` version / commit:
- Python version (`python -V`):
- OS:

## Checklist

- [ ] I ran `pysta check` and it passes
- [ ] I ran `pytest` and it passes
- [ ] This is not a request for a production-grade STA feature (see the
      limitations table in the README)
