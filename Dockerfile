FROM alpine

RUN apk add --no-cache python3 py3-google-auth py3-requests tzdata # ffmpeg
ADD hakidgenai.py prompts.json  /opt/hakidgenai/
ENV PATH="$PATH:/opt/hakidgenai"
VOLUME /var/lib/hakidgenai
ENTRYPOINT ["/opt/hakidgenai/hakidgenai.py"]
