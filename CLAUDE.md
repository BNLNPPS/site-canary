# site-canary

Start from the SWF workspace guide, `../swf-testbed/CLAUDE.md`
(<https://github.com/BNLNPPS/swf-testbed/blob/main/CLAUDE.md>): the
system, the workspace, how to work, production and collaboration. This
file adds what is particular to site-canary, the site testing and health
assessment of PanDA processing resources: probes, payload canaries,
the node guard, the measurement store and the canary page (`README.md`).

- Documents in `docs/`: DESIGN and PLAN for the intent, IMPLEMENTATION
  for what is built (probes, payload canaries, the canary page), and
  SWF_INTEGRATION for how it runs inside swf-monitor (the canary agent,
  its store and its page).
- Developed on `main`. It reaches production with the swf-monitor full
  deploy, which installs it from the local tree, so a change takes effect
  at the next full deploy.
- Tests: `bash tests/run_tests.sh`, in an environment with the package's
  dependencies (the swf-testbed venv).
