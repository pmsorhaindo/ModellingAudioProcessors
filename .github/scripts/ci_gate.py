"""The CI merge gate (#113): pass only if every expected job of this workflow run passed.

It reads the run's jobs from the GitHub API, not the `needs` context: `needs` gives one aggregated result per job id,
and a matrix leg cancelled before a runner picks it up can leave that aggregate "success" (PR #129 re-review). Here
every job is checked by its own name and conclusion:
  1. every name in EXPECTED is present (macOS names only where macOS runs: non-draft PRs and manual dispatch);
  2. every present job concluded "success", except macOS jobs where macOS does not run, which may be "skipped";
  3. no other job is present (a new job must be added to EXPECTED, so it cannot escape the gate), skipped or not; the
     one exception is a skipped macOS matrix job listed under its unexpanded name where macOS does not run.
The gate job itself is excluded. Making the "ci gate" check required on main is a repository setting.

Env: GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, GITHUB_TOKEN (actions: read), MACOS_EXPECTED
("true" / "false").
  python .github/scripts/ci_gate.py               check this run (in CI)
  python .github/scripts/ci_gate.py --self-test   offline checks of the rules
"""
import json
import os
import sys
import urllib.request

GATE = "ci gate"
EXPECTED = [
    "test (valvelab)",
    "test (fullstage)",
    "test (fullstage 6as6 stage)",
    "test (fullstage 6as6 envelope)",
    "test (fullstage 6as6 validation)",
    "test (fullstage 6as6 rest)",
    "test (fullstage cpp 6as6)",
    "test (fit6as6)",
    "test (wdf)",
    "test (fullstage, macos arm64)",
    "test (fullstage 6as6 stage, macos arm64)",
    "test (fullstage 6as6 envelope, macos arm64)",
    "test (fullstage 6as6 validation, macos arm64)",
    "test (fullstage 6as6 rest, macos arm64)",
    "test (fullstage cpp 6as6, macos arm64)",
    "plugin (AU + VST3, macos arm64)",
    "aliasing",
    "aliasing (6as6, 48000 Hz)",
    "aliasing (6as6, 44100 Hz)",
    "aliasing (6as6 C++ core, 48000 Hz)",
    "aliasing (6as6 C++ core, 44100 Hz)",
    "ruff",
]


def is_macos(name):
    return "macos" in name


def check(jobs, macos_expected, expected=EXPECTED):
    """jobs: [{"name", "conclusion"}] of the run (the gate itself included or not). Returns a list of problems."""
    jobs = [j for j in jobs if j["name"] != GATE]
    names = [j["name"] for j in jobs]
    bad = [f"{n}: listed {names.count(n)} times" for n in sorted(set(names)) if names.count(n) > 1]
    for j in jobs:
        n, c = j["name"], j.get("conclusion")
        skip_ok = is_macos(n) and not macos_expected
        if not (c == "success" or (c == "skipped" and skip_ok)):
            bad.append(f"{n}: {c}")
        # An unlisted name passes only as a skipped macOS matrix job that was never expanded: GitHub lists a skipped
        # matrix job once, under its unexpanded name ("test (fullstage${{ ... }}, macos arm64)"). Any other unlisted
        # job fails, skipped or not.
        template = skip_ok and c == "skipped" and "${{" in n
        if n not in expected and not template:
            bad.append(f"{n}: not in the gate's expected jobs (add it to .github/scripts/ci_gate.py)")
    for n in expected:
        if n not in names and (macos_expected or not is_macos(n)):
            bad.append(f"{n}: missing from the run")
    return bad


def run_jobs():
    repo, run, attempt = (os.environ[k] for k in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    jobs, page = [], 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        jobs += [dict(name=j["name"], conclusion=j["conclusion"]) for j in data["jobs"]]
        if len(jobs) >= data["total_count"] or not data["jobs"]:
            return jobs
        page += 1


# The name GitHub gave the skipped macOS matrix job on main's push run 44887386353 (after #128).
TEMPLATE = "test (fullstage${{ ((matrix.shard && format(' {0}', matrix.shard)) || '') }}, macos arm64)"


def self_test():
    ok = [dict(name=n, conclusion="success") for n in EXPECTED]
    lin = [j for j in ok if not is_macos(j["name"])]
    skipped = lin + [dict(name=n, conclusion="skipped") for n in EXPECTED if is_macos(n)]
    cases = [
        ("everything passed", ok, True, True),
        ("draft: macOS skipped", skipped, False, True),
        ("draft: macOS jobs absent", lin, False, True),
        ("ready PR: macOS skipped", skipped, True, False),
        ("a leg cancelled before a runner", [dict(j, conclusion="cancelled") if j["name"] ==
                                             "aliasing (6as6, 44100 Hz)" else j for j in ok], True, False),
        ("a leg missing", [j for j in ok if j["name"] != "aliasing (6as6, 44100 Hz)"], True, False),
        ("the 48 kHz leg twice for the 44.1 kHz one", [dict(j, name="aliasing (6as6, 48000 Hz)") if j["name"] ==
                                                       "aliasing (6as6, 44100 Hz)" else j for j in ok], True, False),
        ("a timeout", [dict(j, conclusion="timed_out") if j["name"] == "test (fullstage 6as6 rest)" else j
                       for j in ok], True, False),
        ("still running", [dict(j, conclusion=None) if j["name"] == "ruff" else j for j in ok], True, False),
        ("an unlisted job", ok + [dict(name="test (new)", conclusion="success")], True, False),
        ("main push: the skipped macOS matrix under its unexpanded name",
         lin + [dict(name=TEMPLATE, conclusion="skipped"),
                dict(name="plugin (AU + VST3, macos arm64)", conclusion="skipped")], False, True),
        ("draft: an unlisted macOS job skipped", lin + [dict(name="test (new, macos arm64)", conclusion="skipped")],
         False, False),
        ("ready PR: the unexpanded macOS matrix skipped", ok + [dict(name=TEMPLATE, conclusion="skipped")], True,
         False),
        ("the gate itself is ignored", ok + [dict(name=GATE, conclusion=None)], True, True),
    ]
    fails = [(name, check(jobs, mac)) for name, jobs, mac, want in cases if (not check(jobs, mac)) != want]
    for name, probs in fails:
        print(f"self-test FAILED: {name}: {probs}")
    print(f"ci gate self-test: {len(cases) - len(fails)} / {len(cases)} cases as expected")
    return not fails


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(0 if self_test() else 1)
    if not self_test():
        sys.exit("ci gate: self-test failed")
    jobs = run_jobs()
    for j in sorted(jobs, key=lambda j: j["name"]):
        print(f"{j['name']}: {j['conclusion']}")
    problems = check(jobs, os.environ.get("MACOS_EXPECTED") == "true")
    if problems:
        sys.exit("ci gate: not passed:\n  " + "\n  ".join(problems))
    print("ci gate: every expected job passed")
