# Token-broker gateway: obtains a real OAuth token and injects it on the way to the MCP server.
FROM python:3.12-alpine
RUN adduser -D -u 10001 gateway
WORKDIR /app
COPY broker.py /app/broker.py
USER 10001
EXPOSE 8931
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8931/gateway-health',timeout=4).status==200 else 1)"
CMD ["python", "-u", "/app/broker.py"]
