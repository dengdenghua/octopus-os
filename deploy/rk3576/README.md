# Echo on RK3576 vendor Debian

Status: initial ARM64 container-overlay adaptation; not a flashable or hardware-validated firmware.

Reference: hypnotic_hub e032c06, scripts/README.md records RK3576 EVB1 V10,
Debian 12 arm64 and systemd. Its bsp/design.md describes image/OTA contracts,
but scripts/README.md section 3.12 explicitly leaves full flashing SOP unfinished.
The checked-out vendor/app_proc directory is application code, not a delivered BSP SDK.

## Ownership

Keep vendor U-Boot, kernel, DTB, modules, GPU/NPU userspace libraries, partitions,
boot configuration and recovery intact. Keep comm_hub/data_engine/sys_manager,
temperature protection, UART/RS485 and watchdog under hypnotic_hub ownership.
Echo runs its existing backend and web desktop in a separate ARM64 container.
This reuses the running vendor kernel, rather than attempting to copy x86 drivers.
There is no NPU acceleration, sleep-data access or device-control integration yet.
Do not mount /dev, /data, Docker socket or Hub control sockets into this profile.
Its named volume isolates Echo state from Hub data. App-store host management is
unavailable in this initial profile; no privileged Docker proxy is installed.

## Build on a Linux machine with Docker Buildx

Install the repository's normal Node/pnpm/Python build prerequisites first.
Build from a reviewed clean source revision (the bundle pipeline rejects dirty sources).

```sh
cd octopus-os
pnpm --dir frontend install --frozen-lockfile
ECHO_LINUX_ARCH=arm64 ./deploy/appliance/prepare-agent-bundle.sh
docker buildx build --platform linux/arm64 --load -t echo-os:rk3576-dev .
docker image inspect echo-os:rk3576-dev --format '{{.Architecture}}'
docker save -o echo-rk3576.tar echo-os:rk3576-dev
```

The existing Dockerfile keeps its pinned bases and hash-locked Python closure.
It now rejects a Codex slice that differs from the Docker target architecture.
If a pinned base or locked wheel is unavailable for ARM64, stop and resolve that
artifact explicitly; do not disable hashes or substitute an unpinned dependency.
Only one architecture's prepared Agent bundle occupies deploy/appliance at a time.
Prepare the x64 bundle again before building the existing amd64 image.

## Board deployment

Obtain a vendor recovery image and record the actual board revision before installation.
Run the read-only probe on the board; missing prerequisites must be handled using
the board vendor's supported procedure. The probe does not install or flash anything.

```sh
python3 deploy/rk3576/probe.py
docker info
docker load -i echo-rk3576.tar
ECHO_ARM_IMAGE=echo-os:rk3576-dev docker compose -f deploy/rk3576/compose.yaml config
ECHO_ARM_IMAGE=echo-os:rk3576-dev docker compose -f deploy/rk3576/compose.yaml up -d --wait
```

Use the board's existing browser at http://127.0.0.1:18000/#/desktop or an SSH
tunnel (`ssh -L 18000:127.0.0.1:18000 user@board`). Initial account setup follows
the existing appliance entrypoint. No new automatic login or auth bypass is added.
The dedicated 18000 port avoids the Hub Test Console's 8080 port.
Stop with the same Compose command plus `down` (without `-v`); vendor services and
the persisted Echo volume remain. This profile does not change the display manager.

## Required acceptance before firmware packaging

1. ARM64 container build, backend health and actual Codex process execution.
2. Login, desktop, local file upload/download and authorized Agent conversation.
3. Parallel Hub operation: temperature-control timing, sensor loss, UART errors,
   watchdog state and resource pressure compared with the pre-install baseline.
4. Reboot persistence, disk-full handling and recovery from a failed Echo update.
5. GPU/NPU integration only after vendor ABI/library versions and licensing are known.
6. Full firmware packaging only after SDK, partition map, recovery and flashing SOP
   are supplied; never apply the existing amd64 ISO/UEFI installer to this board.

No tests on a physical board or complete ARM64 image build have been performed yet.
