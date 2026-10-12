# The test image: everything the suite needs (pygame without a display, zip,
# git, curl). Build once, then run the suite as yourself:
#   docker build -t rb-test:local tests/
#   docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:$PWD -w $PWD rb-test:local \
#       python -m pytest -q -p no:cacheprovider tests
FROM python:3.12-slim
RUN pip install -q pygame==2.6.1 requests pytest pillow python-socketio[client] websocket-client pytest-xdist cryptography \
 && apt-get update -qq && apt-get install -y -qq libglib2.0-0 libzstd1 zip unzip git curl >/dev/null \
 && rm -rf /var/lib/apt/lists/*
