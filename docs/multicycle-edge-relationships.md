# Multicycle path edge relationships

This is the single most error-prone part of SDC, and the reason `pysta`'s engine
looks the way it does. This document derives the edge pairing from scratch and
shows what the two bundled counter-examples actually measure.

Throughout: clock period `T = 10 ns`, rising edges at `0, 10, 20, ...`.
FF1 is the launching register, FF3 the capturing one.

---

## 0. The rule

STA pairs a *launch edge* with a *capture edge* and then compares:

```
slack = (capture_edge + capture_clock_latency - Tsu - Tunc)
      - (launch_edge  + launch_clock_latency  + Tco + sum(comb delays))
```

Everything hinges on how the two edges are paired. Let `t_L` be the launch edge
and `i0` the index of the **first rising edge of the capture clock strictly after
`t_L`**. Then:

```
setup capture edge index = i0 + (M_setup - 1)
hold  capture edge index = setup capture edge index - 1 - M_hold
```

`M_setup` defaults to `1`, `M_hold` to `0`. Every counter-intuitive result comes
from the second line.

---

## 1. Default (M_setup = 1, M_hold = 0)

```
  clk      +---+   +---+   +---+   +---+
        ---+   +---+   +---+   +---+   +---
           0    5   10   15   20   25
  ^        |         |
  launch --+         |        launch edge = 0 ns, i0 = 1
  setup  ------------+        setup edge  = 1st rising edge = 10 ns
  hold   ------------+        hold edge   = setup - 1 = 0th edge = 0 ns
```

* Setup: data launched at 0 ns must settle before **10 ns** ⇒ budget
  `T - Tco - Tsu`.
* Hold: FF3 also captures **at 0 ns**, and the new data was launched at 0 ns too,
  so it must not arrive *too early* ⇒ `Tco + sum(comb) >= Th`.

This is what "hold is analysed one clock edge before setup" means. Note the hold
check uses the *same* launch edge as the setup check — not one period earlier —
which is equivalent because the two clock terms cancel.

---

## 2. `set_multicycle_path -setup 6` only

Now `M_setup = 6, M_hold = 0`:

```
  clk      +---+   +---+   ...   +---+   +---+   +---+
        ---+   +---+   +--- ... ---+   +---+   +---+   +---
           0    5   10    ...    50    55    60    65
  ^        |                                        |
  launch --+                                        |
  setup  ------------------------------------------+   6th rising edge = 60 ns
  hold   ---------------------------------+           5th rising edge = 50 ns  <-- the trap
```

* Setup: budget becomes `6T - Tco - Tsu = 58 ns` — as intended.
* **Hold: the capture edge is dragged to 50 ns**, so the constraint becomes
  `0 + Tco + sum(comb) >= 50 + Th`, i.e. `sum(comb) >= 49.5 ns`.

The tool is now being told: *this path must take at least 49.5 ns, and at most
58 ns*. That is absurd for a real gate-level path, but it is exactly what the
constraints say.

`examples/sdc/ex9b_multicycle_adder_default_hold.sdc` is this case. Measured:

```
reg->reg  FF1/CP -> FF3[0]/D   budget = 58.0000   slack = 57.7300
                               hold edge = 50.0000  hold slack = -49.2300
```

The physical reason the hold check belongs at 0 ns: the data that can disturb the
capture at 60 ns is the data launched at 0 ns (it took six cycles to get there).

---

## 3. Adding `set_multicycle_path -hold 5`

```
hold capture edge index = setup capture edge index - 1 - M_hold
                        = 6 - 1 - 5
                        = 0        ->  0 ns
```

```
  clk      +---+   +---+   ...   +---+   +---+   +---+
        ---+   +---+   +--- ... ---+   +---+   +---+   +---
           0    5   10    ...    50    55    60    65
  ^        |                                        |
  launch --+                                        |
  setup  ------------------------------------------+   60 ns
  hold   --+                                            0 ns   <-- pulled back
```

Now:

```
hold requirement:  sum(comb) >= Th + Tunc - Tco        (~0.5 ns, trivially met)
setup requirement: sum(comb) <= 6T - Tco - Tsu - Tunc  = 58 ns
```

`examples/sdc/ex9_multicycle_adder.sdc` is this case. Measured:

```
reg->reg  FF1/CP -> FF3[0]/D   budget = 58.0000   slack = 57.7300
                               hold edge = 0.0000   hold slack = +0.7700
```

---

## 4. Cheat sheet

| Intent | Constraint | Setup edge | Hold edge |
|---|---|---|---|
| default single cycle | *(none)* | 1st | 0th |
| N cycles | `-setup N` | Nth | (N−1)th |
| N cycles, recommended | `-setup N` + `-hold N-1` | Nth | 0th |

**Rule of thumb: the `-hold` number equals the `-setup` number minus one.**

---

## 5. Restricting the scope with `-through`

In `ex10_multicycle_mixed` two paths leave FF1, but only the multiplier one is
allowed two cycles:

```tcl
set_multicycle_path -setup 2 -from FF1/CP -through Multiply/out -to FF2/D
set_multicycle_path -hold  1 -from FF1/CP -through Multiply/out -to FF2/D
```

Measured (`pysta check`):

| Path | hits `-through Multiply/out` | setup/hold | budget |
|---|---|---|---|
| FF1/CP → FF2/D (multiply) | yes | 2 / 1 | 17.9 ns |
| FF1/CP → FF3/D (xor) | no | 1 / 0 | 7.9 ns |

Drop the `-through` and both paths become 2-cycle; the xor path is then
over-relaxed and the tool may produce logic that cannot actually run at one
cycle per clock.

> The budget is 17.9 ns rather than 18.0 ns because cell delay is load-dependent:
> `FF1/Q` drives two sinks, so `Tco` is looked up as 1.1 ns instead of 1.0 ns,
> giving `20 - 1.1 - 1.0 = 17.9`.

---

## 6. See it for yourself

```bash
pysta report ex9        # and ex9b
pysta run               # then open out/timing_visualization.html
```

The waveform for each example marks the launch edge, the setup capture edge and
the hold capture edge as separate dashed lines. In `ex9b` the hold line visibly
sits at 50 ns and the hold violation is drawn as a red band.
