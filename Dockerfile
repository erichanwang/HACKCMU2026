# Clean-room Linux build/test image: the Swift toolchain here already links
# against this base image's libxml2/libicu, so no compat-lib hacks needed
# (those are only required on the maintainer's Ubuntu 26.04 dev box, see
# swift/PackPhysics/scripts/setup-linux.sh).
FROM swift:6.3-noble

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "python3 -m unittest discover && cd swift/PackPhysics && swift test"]
