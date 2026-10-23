# AC-check voltage regressions

The two phase-voltage extrema must remain numeric. Storing `fminf`/`fmaxf`
results in `bool` turns nonzero values into 1, masking voltage excursions.

From a Git clone containing public v1.1 (`5e2396c`), using Python 3 with PyYAML
and a GCC/Clang-compatible C11 compiler:

```sh
python3 validation/c/ac_checks_A/run_regression.py
```

Use `CC` or `--cc` to select a compiler. Build artifacts, individual scenario
logs and a source-hash manifest remain in a new temporary directory; use
`--output-dir` to choose a new directory explicitly. Source archives are not
supported. The runner does not fetch missing Git history, access the network,
generate files in the source tree or install packages.

The runner reads and verifies the original component from public v1.1 Git
history, then compares it with the current working-tree source. It snapshots
and hashes the current helpers, schema and generator for both variants,
generates headers, and compiles the real component with `timers.c` and `cmplx.c`.
Current source comments and unrelated edits are allowed; changed behavior must
still satisfy the regression checks. Every scenario runs in a fresh process
because component timers include function-static state.

Expected result: **50 corrected scenarios / 199 checks; 26 original failures
corrected; 15 controls with identical observed state logs**. Coverage includes
unequal phases and permutations, both voltage-trip tiers, inclusive
qualification and strict trip boundaries, assertion/clearing/recovery,
frequency and negative-sequence controls, signed-zero and arithmetic probes.
Crashes, missing results and unexpected original passes fail the runner.

The fixtures use positive 3.5-ms assertion/qualification and 2.5-ms clearing
delays at a 1-ms call interval. These are test timings, not protection settings.
Negative phase values test signed arithmetic, not valid RMS measurements.
Timer checks allow 1e-7 s for float rounding. Windows DLL loading, Simulink,
blackstart permission and full electrical behavior are outside these tests.
Validated on macOS with Apple Clang 21.0.0; other platforms are unverified.
