ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
ENV PIP_BREAK_SYSTEM_PACKAGES=1

RUN apk add --no-cache \
    bash \
    python3 \
    py3-pip \
    tzdata

WORKDIR /opt/thread-observability

COPY app/ /opt/thread-observability/app/
COPY rootfs/ /

RUN sed -i 's/\r$//' /etc/cont-init.d/00-init.sh \
        /etc/s6-overlay/s6-rc.d/core/run \
        /etc/s6-overlay/s6-rc.d/mcp/run && \
    chmod 755 /etc/cont-init.d/00-init.sh \
              /etc/s6-overlay/s6-rc.d/core/run \
              /etc/s6-overlay/s6-rc.d/mcp/run && \
    pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir /opt/thread-observability/app

# s6-overlay is provided by Home Assistant base images.
CMD ["/init"]
