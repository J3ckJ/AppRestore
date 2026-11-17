#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive QT_QPA_PLATFORM=offscreen PIP_CERT=/etc/ssl/certs/ca-certificates.crt PIP_NO_CACHE_DIR=1
export PATH="/opt/venv/bin:/usr/local/go/bin:$PATH"
apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git build-essential python3 python3-venv python3-dev \
    libegl1 libgl1 libopengl0 libfontconfig1 libfreetype6 libxkbcommon0 libdbus-1-3 libxcb-cursor0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 libglib2.0-0 libnss3 \
    && rm -rf /var/lib/apt/lists/*
if [ -f /run/secrets/extra_ca ]; then cp /run/secrets/extra_ca /usr/local/share/ca-certificates/build-proxy.crt; update-ca-certificates; fi
curl -fL https://go.dev/dl/go1.26.5.linux-amd64.tar.gz -o /tmp/go.tar.gz \
    && echo "5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053  /tmp/go.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/go.tar.gz -C /usr/local && rm /tmp/go.tar.gz
python3 -m venv /opt/venv
pip install --require-hashes --only-binary=:all: --no-deps -r requirements/build.lock \
    && pip install --require-hashes --only-binary=:all: --no-deps --find-links requirements/wheels -r requirements/runtime.lock \
    && pip install --no-deps --no-build-isolation . \
    && pip install --only-binary=:all: -r requirements/gui-build.txt
bash packaging/build-ipatool.sh linux-amd64
pyinstaller packaging/apprestore-cli.spec --noconfirm --clean --distpath dist-cli --workpath build-cli \
    && python packaging/package_cli.py --dist dist-cli --out linux-dist --expect-version "$(python -c 'import apprestore_core; print(apprestore_core.__version__)')" \
       --ipatool-archive dist/ipatool/ipatool-2.8.1-linux-amd64.tar.gz
pyinstaller packaging/apprestore-gui.spec --noconfirm --clean \
    && python packaging/package_gui.py --out linux-dist --expect-version "$(python -c 'import apprestore_core; print(apprestore_core.__version__)')" \
       --ipatool-archive dist/ipatool/ipatool-2.8.1-linux-amd64.tar.gz
rm -rf /root/.cache /root/go /src/build /src/build-cli /tmp/*
