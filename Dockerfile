# Engine container for the IBKR leg, co-located with ib-gateway on the
# gateway VM (see docs/IBKR_GATEWAY.md, deploy/ibkr-gateway/). Only the IBKR
# broker runs here; Robinhood/Alpaca stay on the Render worker.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
# ib_async is commented in requirements (gateway-box only); install it here.
RUN pip install --no-cache-dir -r requirements.txt ib_async

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py", "--broker", "ibkr", "run"]
