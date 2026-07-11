ARG BROWSER_BASE_IMAGE=chromedp/headless-shell:latest
FROM ${BROWSER_BASE_IMAGE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f
