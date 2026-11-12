FROM fc-e2b-registry.cn-hangzhou.cr.aliyuncs.com/runtime-v2/base@sha256:57573087625efc9ff4713c03bff43adc2feb286b407d3d65a70729e443760e5d
USER root
ARG SOURCE_REVISION
LABEL org.opencontainers.image.source="https://github.com/theAfish/open-agent-world"
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
COPY plugins/xrd /source/plugins/xrd
COPY deploy/aliyun/xrd_template /source/deploy/aliyun/xrd_template
RUN test "${#SOURCE_REVISION}" -eq 40 \
    && printf '%s' "$SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$' \
    && sed -i 's/\r$//' /source/deploy/aliyun/xrd_template/build.sh \
    && OAW_XRD_SOURCE_REVISION="$SOURCE_REVISION" bash /source/deploy/aliyun/xrd_template/build.sh /source \
    && rm -rf /source
# Preserve the official envd bootstrap. OAW runs workers as the Sandbox user,
# through its existing non-root relay; the image bootstrap is not the worker.
