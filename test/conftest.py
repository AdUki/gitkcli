# Keep pytest out of the generated fixture repos: test/repo and test/remotes
# contain dummy test_*.py files (fixture content) that would otherwise be
# collected and error. Paths are relative to this conftest (test/).
collect_ignore = ["repo", "remotes"]
