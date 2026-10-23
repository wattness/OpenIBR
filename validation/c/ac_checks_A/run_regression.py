#!/usr/bin/env python3
"""Compile and compare original and corrected AC-check component regressions."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = Path("c_language_library/companion_algorithms/ac_checks_A/ac_checks_A.c")
SCHEMA = Path("c_language_library/companion_algorithms/ac_checks_A_schema.yaml")
GENERATOR = Path("schema_tools/generate_header.py")
HELPERS = [Path("c_language_library/helper_lib") / name for name in (
    "timers.c", "timers.h", "cmplx.c", "cmplx.h", "helper_macros.h", "common_types.h",
)]
BASELINE = "5e2396cef8e57765c6a8fefe34fc379c3a60c163"
ORIGINAL_SHA256 = "ee5ea0b383de265ab67289bca4a9b125545ecc2f050d31f604ef5733c5624525"
UNCHANGED = {
    "nominal_unequal", "zero_all", "zero_one_phase", "signed_zero",
    "nominal_35kV", "rating_floor", "frequency_under1", "frequency_under2",
    "frequency_over1", "frequency_over2", "imbalance_reject", "imbalance_inclusive",
    "qualification_bypass", "frequency_pulse_clear_memory", "timer_helpers",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def original_variant(fixed):
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{BASELINE}:{COMPONENT.as_posix()}"],
        capture_output=True, timeout=30,
        env=dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0"),
    )
    require(result.returncode == 0,
            "Public v1.1 baseline unavailable; use a Git clone containing " + BASELINE)
    original = result.stdout
    require(digest(original) == ORIGINAL_SHA256, "Public v1.1 source hash mismatch")
    require(fixed != original, "Current source is unchanged from the original baseline")
    return original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", help="Compiler command; defaults to CC or cc")
    parser.add_argument("--output-dir", type=Path, help="New directory for build artifacts and logs")
    args = parser.parse_args()
    compiler = shlex.split(args.cc or os.environ.get("CC", "cc"))
    if not compiler or shutil.which(compiler[0]) is None:
        parser.error("A GCC/Clang-compatible C11 compiler is required")
    if shutil.which("git") is None:
        parser.error("Git and the public v1.1 baseline are required")

    out = args.output_dir.expanduser().resolve() if args.output_dir else None
    if out is None:
        out = Path(tempfile.mkdtemp(prefix="openibr-ac-checks-"))
    else:
        out.mkdir(parents=True, exist_ok=False)
    record = {"status": "preparing", "baseline_commit": BASELINE,
              "fresh_process_per_scenario": True, "inputs": [], "commands": [], "cases": []}

    def save():
        (out / "manifest.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")

    def run(argv, label):
        result = subprocess.run(argv, cwd=out, capture_output=True, text=True, timeout=30)
        for stream in ("stdout", "stderr"):
            (out / f"{label}.{stream}").write_text(getattr(result, stream))
        record["commands"].append({
            "argv": argv, "exit_code": result.returncode,
            **{stream: f"{label}.{stream}" for stream in ("stdout", "stderr")},
            **{stream + "_sha256": digest(getattr(result, stream).encode())
               for stream in ("stdout", "stderr")},
        })
        save()
        return result

    try:
        fixed = (ROOT / COMPONENT).read_bytes()
        original = original_variant(fixed)
        record["original_source_sha256"] = digest(original)
        record["fixed_source_sha256"] = digest(fixed)
        require(run([*compiler, "--version"], "compiler-version").returncode == 0,
                "Compiler version command failed")
        shared = out / "shared"
        for path in [*HELPERS, SCHEMA, GENERATOR]:
            content = (ROOT / path).read_bytes()
            target = shared / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            record["inputs"].append({"path": path.as_posix(), "sha256": digest(content)})
        harness = out / "harness.c"
        harness.write_bytes(Path(__file__).with_name("harness.c").read_bytes())
        record["harness_sha256"] = digest(harness.read_bytes())
        record["runner_sha256"] = digest(Path(__file__).read_bytes())
        headers = shared / COMPONENT.parent
        generated = run([sys.executable, "-I", "-B", str(shared / GENERATOR),
                         str(shared / SCHEMA), str(headers)], "generate-headers")
        require(generated.returncode == 0, "Header generation failed; Python requires PyYAML")
        header_names = ("acChecks_A_autogen.h", "acChecks_A_private_autogen.h")
        record["headers"] = [{"name": name, "sha256": digest((headers / name).read_bytes())}
                             for name in header_names]

        binaries = {}
        for variant, content in (("original", original), ("fixed", fixed)):
            directory = out / variant
            source = directory / COMPONENT
            source.parent.mkdir(parents=True)
            source.write_bytes(content)
            for path in HELPERS:
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(shared / path, target)
            for name in header_names:
                shutil.copyfile(headers / name, source.parent / name)
            binary = directory / "ac_checks_test"
            command = [*compiler, "-std=c11", "-O0", "-g", "-Wall", "-Wextra", "-Wconversion",
                       "-I", str(source.parent), "-I", str(directory / "c_language_library/helper_lib"),
                       str(harness), str(source), str(directory / HELPERS[0]),
                       str(directory / HELPERS[2]), "-lm", "-o", str(binary)]
            require(run(command, "compile-" + variant).returncode == 0,
                    f"{variant} compilation failed")
            binaries[variant] = binary

        listed = run([str(binaries["fixed"]), "--list"], "scenario-list")
        require(listed.returncode == 0, "Scenario listing failed")
        scenarios = [json.loads(line) for line in listed.stdout.splitlines()]
        names = {row["name"] for row in scenarios}
        require(len(names) == len(scenarios) == 50, "Expected 50 unique scenarios")
        require(UNCHANGED <= names, "Observable controls are missing")
        record["status"] = "testing"
        for scenario in scenarios:
            name = scenario["name"]
            results = {}
            for variant in ("original", "fixed"):
                result = run([str(binaries[variant]), name], variant + "-" + name)
                events = [json.loads(line) for line in result.stdout.splitlines()]
                require(bool(events), f"{variant}/{name}: missing result")
                final = events[-1]
                require(final["event"] == "result" and final["name"] == name and final["checks"] > 0,
                        f"{variant}/{name}: malformed result")
                expected_failure = variant == "original" and scenario["original_should_fail"]
                require(result.returncode == (1 if expected_failure else 0),
                        f"{variant}/{name}: unexpected exit code")
                require((final["failures"] > 0) == expected_failure,
                        f"{variant}/{name}: unexpected check failures")
                results[variant] = {"exit_code": result.returncode, "checks": final["checks"],
                                    "failures": final["failures"],
                                    "stdout_sha256": digest(result.stdout.encode())}
            if name in UNCHANGED:
                require(results["original"]["stdout_sha256"] == results["fixed"]["stdout_sha256"],
                        f"Observable control changed: {name}")
            record["cases"].append({**scenario, **results})

        summary = {"scenarios": len(scenarios),
                   "fixed_checks": sum(row["fixed"]["checks"] for row in record["cases"]),
                   "original_fail_fixed_pass": sum(row["original_should_fail"] for row in scenarios),
                   "unchanged_observable_controls": len(UNCHANGED)}
        require(summary["fixed_checks"] == 199 and summary["original_fail_fixed_pass"] == 26,
                "Unexpected regression coverage")
        record.update(status="passed", summary=summary)
        save()
        print(json.dumps(summary, sort_keys=True))
        print(f"Artifacts: {out}")
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        record.update(status="failed", error=str(error))
        save()
        print(f"Regression failed: {error}\nArtifacts: {out}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
