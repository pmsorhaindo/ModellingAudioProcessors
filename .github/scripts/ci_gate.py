"""The CI merge gate (#113): pass only if every expected job of this workflow run passed.

It reads the run's jobs from the GitHub API, not the `needs` context: `needs` gives one aggregated result per job id,
and a matrix leg cancelled before a runner picks it up can leave that aggregate "success" (PR #129 re-review). Here
every job is checked by its own name and conclusion:
  1. every required name in EXPECTED is present (the macOS fullstage jobs are optional on drafts and main,
     and the macOS plugin job is optional on drafts);
  2. every present job concluded "success", except optional jobs, which may be "skipped";
  3. no other job is present (a new job must be added to EXPECTED, so it cannot escape the gate), skipped or not; the
     one exception is a skipped macOS matrix job listed under its unexpanded name where macOS does not run.
The gate job itself is excluded. Making the "ci gate" check required on main is a repository setting.

Env: GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, GITHUB_TOKEN (actions: read),
FULLSTAGE_MACOS_EXPECTED and PLUGIN_EXPECTED ("true" / "false").
  python .github/scripts/ci_gate.py               check this run (in CI)
  python .github/scripts/ci_gate.py --self-test   offline checks of the rules
"""
import json
import os
import sys
import urllib.request

GATE = "ci gate"
PLUGIN = "plugin (AU + VST3, macos arm64)"
# The name GitHub gave the skipped macOS matrix job on main's push run 44887386353 (after #128).
TEMPLATE = "test (fullstage${{ ((matrix.shard && format(' {0}', matrix.shard)) || '') }}, macos arm64)"
EXPECTED = [
    "test (valvelab)",
    "test (fullstage)",
    "test (fullstage 6as6 stage)",
    "test (fullstage 6as6 envelope)",
    "test (fullstage 6as6 validation)",
    "test (fullstage 6as6 rest)",
    "test (fullstage cpp 6as6)",
    "test (fullstage cpp 6as6 live)",
    "test (fit6as6)",
    "test (wdf)",
    "test (fullstage, macos arm64)",
    "test (fullstage 6as6 stage, macos arm64)",
    "test (fullstage 6as6 envelope, macos arm64)",
    "test (fullstage 6as6 validation, macos arm64)",
    "test (fullstage 6as6 rest, macos arm64)",
    "test (fullstage cpp 6as6, macos arm64)",
    "test (fullstage cpp 6as6 live, macos arm64)",
    PLUGIN,
    "aliasing",
    "aliasing (6as6, 48000 Hz)",
    "aliasing (6as6, 44100 Hz)",
    "aliasing (6as6 C++ core, 48000 Hz)",
    "aliasing (6as6 C++ core, 44100 Hz)",
    "ruff",
]


def is_macos(name):
    return "macos" in name


def check(jobs, fullstage_macos_expected, plugin_expected, expected=EXPECTED):
    """jobs: [{"name", "conclusion"}] of the run (the gate itself included or not). Returns a list of problems."""
    jobs = [j for j in jobs if j["name"] != GATE]
    names = [j["name"] for j in jobs]
    optional = set()
    if not fullstage_macos_expected:
        optional.update(n for n in expected if is_macos(n) and n != PLUGIN)
    if not plugin_expected:
        optional.add(PLUGIN)
    bad = [f"{n}: listed {names.count(n)} times" for n in sorted(set(names)) if names.count(n) > 1]
    for j in jobs:
        n, c = j["name"], j.get("conclusion")
        template = n == TEMPLATE and not fullstage_macos_expected and c == "skipped"
        skip_ok = n in optional or template
        if not (c == "success" or (c == "skipped" and skip_ok)):
            bad.append(f"{n}: {c}")
        # An unlisted name passes only as a skipped macOS matrix job that was never expanded: GitHub lists a skipped
        # matrix job once, under its unexpanded name ("test (fullstage${{ ... }}, macos arm64)"). Any other unlisted
        # job fails, skipped or not.
        if n not in expected and not template:
            bad.append(f"{n}: not in the gate's expected jobs (add it to .github/scripts/ci_gate.py)")
    for n in expected:
        if n not in names and n not in optional:
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


def self_test():
    ok = [dict(name=n, conclusion="success") for n in EXPECTED]
    lin = [j for j in ok if not is_macos(j["name"])]
    skipped = lin + [dict(name=n, conclusion="skipped") for n in EXPECTED if is_macos(n)]
    main = lin + [dict(name=TEMPLATE, conclusion="skipped")]
    cases = [
        ("everything passed", ok, True, True, True),
        ("draft: macOS skipped", skipped, False, False, True),
        ("draft: macOS jobs absent", lin, False, False, True),
        ("ready PR: macOS skipped", skipped, True, True, False),
        ("a leg cancelled before a runner", [dict(j, conclusion="cancelled") if j["name"] ==
                                             "aliasing (6as6, 44100 Hz)" else j for j in ok], True, True, False),
        ("a leg missing", [j for j in ok if j["name"] != "aliasing (6as6, 44100 Hz)"], True, True, False),
        ("the 48 kHz leg twice for the 44.1 kHz one", [dict(j, name="aliasing (6as6, 48000 Hz)") if j["name"] ==
                                                       "aliasing (6as6, 44100 Hz)" else j for j in ok], True, True, False),
        ("a timeout", [dict(j, conclusion="timed_out") if j["name"] == "test (fullstage 6as6 rest)" else j
                       for j in ok], True, True, False),
        ("still running", [dict(j, conclusion=None) if j["name"] == "ruff" else j for j in ok], True, True, False),
        ("an unlisted job", ok + [dict(name="test (new)", conclusion="success")], True, True, False),
        ("main push: plugin passed, macOS fullstage skipped",
         main + [dict(name=PLUGIN, conclusion="success")], False, True, True),
        ("main push: plugin skipped", main + [dict(name=PLUGIN, conclusion="skipped")], False, True, False),
        ("main push: plugin failed", main + [dict(name=PLUGIN, conclusion="failure")], False, True, False),
        ("main push: plugin missing", main, False, True, False),
        ("draft: an unlisted macOS job skipped", lin + [dict(name="test (new, macos arm64)", conclusion="skipped")],
         False, False, False),
        ("ready PR: the unexpanded macOS matrix skipped",
         ok + [dict(name=TEMPLATE, conclusion="skipped")], True, True, False),
        ("the gate itself is ignored", ok + [dict(name=GATE, conclusion=None)], True, True, True),
    ]
    fails = []
    for name, jobs, mac, plugin, want in cases:
        problems = check(jobs, mac, plugin)
        if (not problems) != want:
            fails.append((name, problems))
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
    problems = check(jobs, os.environ["FULLSTAGE_MACOS_EXPECTED"] == "true", os.environ["PLUGIN_EXPECTED"] == "true")
    if problems:
        sys.exit("ci gate: not passed:\n  " + "\n  ".join(problems))
    print("ci gate: every expected job passed")
