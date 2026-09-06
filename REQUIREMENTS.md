# Runtime requirements

This recipe is a launcher around a compatible upstream runtime. Before using
it, prepare the following on every rank:

1. Four compatible GB10-class nodes with NVIDIA drivers, Docker plus the
   NVIDIA container runtime, a reachable IPv4 RoCEv2 fabric, passwordless SSH
   from the controller, and `/dev/infiniband` available to Docker.
2. A recorded compatible image digest on every rank, built from the upstream
   source pinned in [README.md](README.md#pinned-inputs-and-effective-runtime). The source
   qualification used independently built per-rank digests, not one common
   digest; all ranks nevertheless had the same accepted sparse-MLA source and
   patched hashes. The patch refuses every other preimage.
3. The same upstream checkout on all ranks at `c190db1`, with its `overlay/`
   directory. Install this export's pinned
   `recipe/chat_template-20260904.jinja` as `files/chat_template.jinja` on
   every runtime root; the selected template is not the unmodified upstream
   template. `node-launch.sh` mounts and executes the required upstream overlay
   patches; this export does not substitute or recreate those dependencies.
4. Local Hugging Face cache snapshots for the pinned EXL3 checkpoint and
   DFlash2 drafter. The launcher runs offline and fails closed when either
   selected snapshot is absent.
5. A per-site `cluster.env` based on `cluster.env.example`, with actual
   interfaces, HCA names, rank addresses, image digests and paths.

The selected dual-HCA setup requires an IPv4 RoCEv2 GID for both named HCAs in
`NCCL_IB_ADDR_RANGE`. Align host and switch PFC/ECN and the data/control QoS
priorities before preflight. The launcher verifies GID availability and
connectivity, but does not install network QoS policy.

For the example address range, use primary addresses `10.0.0.1` through
`10.0.0.4` and secondary addresses `10.1.0.1` through `10.1.0.4`, adapting
both to your network. On Spark, the two logical HCAs can address the same
physical QSFP port through separate PCIe links. The selected data class uses
DSCP 26 / PCP 3 with PFC priority 3 and ECN; control uses DSCP 48 / PCP 6.

Run `./recipe/tp4-cluster.sh cluster.env preflight` before launch. The
preflight confirms all-rank reachability and image presence; it cannot prove
that a different image, driver, fabric or model revision will reproduce the
reported result.
