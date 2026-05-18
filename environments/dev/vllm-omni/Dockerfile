# syntax=docker/dockerfile:1.7
# vllm/vllm-openai:v0.20.0, linux/amd64 manifest digest.
FROM vllm/vllm-openai@sha256:77797441eae630c2e79eefa03957b3d61a278670f2a9928d64ce102e7a0790cc

# vLLM-Omni v0.20.0 tag resolved to this commit.
ENV VLLM_OMNI_COMMIT=4a24a517abc7769b1399ded594558a3fe8269872

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && git config --global http.version HTTP/1.1 \
    && git config --global http.lowSpeedLimit 1000 \
    && git config --global http.lowSpeedTime 60 \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    set -eux; \
    for attempt in 1 2 3 4 5; do \
        python3 -m pip install --retries 10 --timeout 120 \
            "git+https://github.com/vllm-project/vllm-omni.git@${VLLM_OMNI_COMMIT}" \
            && break; \
        if [ "${attempt}" = "5" ]; then \
            exit 1; \
        fi; \
        sleep "$((attempt * 10))"; \
    done
