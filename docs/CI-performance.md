# CI performance

All backend, browser, lint, documentation and Docker checks run on pull requests.
The latency target is five minutes from the first job starting to the last job
finishing. Queue time before the first job is additional and depends on CircleCI
capacity and the account's concurrency limit.

## Measured baseline (October 25, 2026)

The investigation downloaded the latest 60 CircleCI jobs through
`https://circleci.com/api/v1.1/project/github/MIT-Tab/mit-tab?limit=60`, then
downloaded step timings, logs and JUnit artifacts for representative builds.
These jobs cover October 24–25, before this optimization.

| Successful job | Samples | Median | Range |
| --- | ---: | ---: | ---: |
| Backend (`build`) | 10 | 9.44 min | 7.77–11.62 min |
| Browser (`web-tests`) | 12 | 4.22 min | 3.34–4.81 min |
| Lint | 12 | 1.42 min | 1.04–1.60 min |
| Docker | 12 | 1.38 min | 1.31–1.52 min |
| Documentation | 10 | 0.43 min | 0.19–0.62 min |

The nine workflows with all five jobs successful had a median elapsed time of
9.45 minutes (8.11–11.62 minutes), measured from the earliest job start to the
latest job finish. Failed and canceled jobs were excluded from these medians.

Representative backend builds:

| Build | Test step durations across four shards |
| --- | --- |
| [5374](https://circleci.com/gh/MIT-Tab/mit-tab/5374) | 443, 165, 175, 285 sec |
| [5364](https://circleci.com/gh/MIT-Tab/mit-tab/5364) | 224, 185, 144, 531 sec |
| [5357](https://circleci.com/gh/MIT-Tab/mit-tab/5357) | 241, 644, 181, 195 sec |

## Changes

The splitter used imported module names, while JUnit used paths relative to the
repository. Tests in directories without `__init__.py` therefore had different
identifiers: for example, `test_min_byes.TestMinByes` versus
`mittab.libs.tests.tab_logic.test_min_byes.TestMinByes`. CircleCI logged
"No timing found" for these classes, including the slowest pairing tests.

`pytest.ini` now uses `--import-mode=importlib`, so the splitter and JUnit agree.
The lint job runs `bin/check-ci-test-timings` against the entire collection to
prevent this regression. It compares the installed splitter and JUnit reporter,
including tests outside Python packages.

Backend parallelism increases from four to six, and browser parallelism from two
to four. Both explicitly use `medium` resources. Replaying build 5374's class
durations with a longest-first assignment gives a maximum of 258 seconds with
four shards, or 192 seconds with six, before setup/reporting. This is a planning
estimate, not an observed optimized run or a guarantee. New tests, timing history,
runner variation and queueing can change actual results.

Browser pairing tests wait for the judge-assignment button to become clickable
instead of sleeping ten seconds each. This saves up to twenty seconds while
continuing to wait up to thirty seconds on a slow runner.

The unused `.test_durations` caches are removed: the installed splitter reads
CircleCI's stored JUnit timings, not that file. Both test jobs print their twenty
slowest tests to help identify the next bottleneck.

No tests, coverage collection or PR gates are removed or moved after merge.
The change raises peak workflow containers from nine to thirteen, assuming all
jobs start together; account concurrency limits can introduce queueing. Total
test work is unchanged, but additional shards repeat setup and fixture loading.
CircleCI remains the provider because test execution and imbalance dominate
the measured critical path; a provider migration would not fix the identifier bug.

## Verify a change

Run `uv run python bin/check-ci-test-timings`, the backend and browser suites, and
the normal lint checks. To verify that the identifier guard detects the original
bug, run `uv run python bin/check-ci-test-timings -o addopts=`; this should fail.

After CI completes, download the completed job's step data and compare the maximum
shard runtime and workflow elapsed time with the baseline above. Use the repository
CI watcher to wait for completion; do not manually poll live runs.
