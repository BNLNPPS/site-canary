# site-canary

Lightweight site testing and health assessment for PanDA processing
resources, written for ePIC but as an agnostic tool: continuous
functional probing of the distributed sites, automated exclusion and
recovery acting through native PanDA mechanisms, and a live record of
site state and capability.

The role is in part the one HammerCloud has filled for ATLAS:
site health testing as input to exclusion and recovery, and dynamic
documentation of what sites can actually do. site-canary draws on the
capabilities now in-house in PanDA, which were not there when HC was
designed in the 2000s. The scope is also extended to use every PanDA
job as a potential measurement tool, with due consideration to the
importance of keeping it highly lightweight, non-intrusive and efficient.
A rider carried by every
pilot maps the processing landscape node by node as real work flows;
dedicated probe jobs cover the gaps and run deliberate capability
checks. Crucially, the rider first performs a fast inexpensive
check on whether an assessment of the node is warranted, and only
then proceeds. It does not add processing burden to every PanDA worker.

The name is the coal mine canary: the sentinel carried in
by every crew, that fails visibly. Time-to-verdict is what makes a
canary a canary, and prompt verdicts and early flagging of problematic
sites are designed in from the start.

site-canary is part of the ePIC workflow management system family,
alongside the [swf repositories](https://github.com/BNLNPPS/swf-testbed),
[corun-ai](https://github.com/BNLNPPS/corun-ai), and
[snapper-ai](https://github.com/BNLNPPS/snapper-ai), documented at the
[ePIC WFMS documentation](https://epic-wfms-docs.readthedocs.io/).

Status: design. The design considerations, including the decision record
on the HammerCloud path, are in [docs/DESIGN.md](docs/DESIGN.md).
