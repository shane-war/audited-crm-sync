# Contributing

Use Python 3.9 or newer. The default runtime and tests use only the standard library.

Run `python3 -m unittest discover -s tests -v` and the README demonstration before submitting changes. Add failure-case tests when changing persistence, retries, leases, or mapping rules.

Only invented fixtures belong in this repository. Do not add employer source, production exports, environment files, credentials, certificates, or real employee/customer records. Keep examples independent of internal infrastructure. Generated databases are ignored.

After staging changes, run `python3 scripts/check_portfolio.py`. This lightweight check detects common credential formats and prohibited artifacts; it is not a comprehensive secret scanner or an ownership review.

Document limitations beside the feature they affect. Do not describe mock integrations as live vendor connections or fabricate performance measurements.
