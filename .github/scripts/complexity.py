"""Scores a PR's merge result against its base and writes a markdown report."""
import json
import os
import subprocess
import tempfile

TEMP = os.environ["RUNNER_TEMP"]


def run(tree, *cmd):
    return subprocess.run(cmd, cwd=tree, check=True, capture_output=True, text=True).stdout


def score(tree):
    radon = json.loads(run(tree, "radon", "cc", "-j", ".")).values()
    # radon lists each method on its own as well, so class rows would double count.
    cc = [b["complexity"] for v in radon if isinstance(v, list) for b in v if b["type"] != "class"]
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "complexipy.json")
        # complexipy exits 1 if any file fails to parse but still writes the rest; a crash leaves no file.
        subprocess.run(["complexipy", ".", "--isolated", "-i", "-q", "--output-format", "json", "--output", out],
                       cwd=tree, capture_output=True)
        with open(out) as f:
            cog = [item["complexity"] for item in json.load(f)]
    return {
        "Functions with CC > 10": sum(c > 10 for c in cc),
        "Functions with cognitive > 15": sum(c > 15 for c in cog),
        "Total cognitive complexity": sum(cog),
        "Functions analyzed": len(cog),
        "Files radon skipped": sum(not isinstance(v, list) for v in radon),
    }


prev, cur = score(os.path.join(TEMP, "base")), score(".")
base_sha = run(".", "git", "rev-parse", "HEAD^1")[:7]
body = "\n".join([
    "<!-- complexity-report -->",
    "### Complexity report",
    f"Previous: base `{base_sha}`. Current: `{os.environ['HEAD_SHA'][:7]}` merged into base.",
    "",
    "| Metric | Previous | Current | Delta |",
    "| --- | ---: | ---: | ---: |",
    *[f"| {k} | {prev[k]} | {cur[k]} | {cur[k] - prev[k]:+d} |" for k in cur],
    "",
])
with open(os.path.join(TEMP, "report.md"), "w") as f:
    f.write(body)
with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
    f.write(body)
