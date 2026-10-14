# LKH-3 (Keld Helsgaun), the solver for the Walmart tour.
# The tarball is fetched on the host first (Docker's build network can't reach ruc.dk here):
#   curl -sL -o experiments/walmart/lkh/LKH-3.0.13.tgz http://webhotel4.ruc.dk/~keld/research/LKH-3/LKH-3.0.13.tgz
FROM gcc:13
COPY LKH-3.0.13.tgz /tmp/
RUN tar xzf /tmp/LKH-3.0.13.tgz && make -C LKH-3.0.13 && cp LKH-3.0.13/LKH /usr/local/bin/LKH
WORKDIR /w
ENTRYPOINT ["LKH"]
