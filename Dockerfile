FROM python:3.12-slim

# Install Node.js 20 (required for @arizeai/phoenix-mcp via npx)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npm@latest --quiet \
    && rm -rf /var/lib/apt/lists/*

# Pre-cache the Phoenix MCP package so the container doesn't need to download
# it at runtime (speeds up first improvement loop call)
RUN npx -y @arizeai/phoenix-mcp@latest --version 2>/dev/null || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --quiet --root-user-action=ignore --upgrade pip \
    && pip install --no-cache-dir --quiet --root-user-action=ignore -r requirements.txt

COPY . .

ENV PORT=8080
ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_ENABLE_CORS=false
ENV STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false

EXPOSE 8080

CMD ["streamlit", "run", "streamlit_app.py", \
     "--server.port=8080", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
