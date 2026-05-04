FROM node:24-bookworm

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-venv python3-pip \
  && rm -rf /var/lib/apt/lists/*

COPY scripts/modeling/requirements.txt scripts/modeling/requirements.txt
RUN python3 -m venv /opt/market-pulse-model \
  && /opt/market-pulse-model/bin/pip install --upgrade pip \
  && /opt/market-pulse-model/bin/pip install -r scripts/modeling/requirements.txt

COPY . .

ENV MODEL_PYTHON=/opt/market-pulse-model/bin/python
ENV PORT=8080

CMD ["node", "scripts/local-dashboard-server.mjs"]
