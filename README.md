# ALPR FTP Server

Automatic License Plate Recognition with built-in FTP server for camera motion-detection uploads.

Detected plates can trigger webhooks for automation (gate openers, Home Assistant, alerts, etc.).

## Quick Start

```bash
# Build
docker build -t alpr-ftp .

# Run (basic - stdout logging only)
docker run -d -p 21:21 -p 21000-21010:21000-21010 \
  -e FTP_USER=camera \
  -e FTP_PASS=yourpassword \
  --name alpr alpr-ftp

# Run (with webhook)
docker run -d -p 21:21 -p 21000-21010:21000-21010 \
  -e FTP_USER=camera \
  -e FTP_PASS=yourpassword \
  -e WEBHOOK_URL=http://192.168.1.50/relay/0?turn=on \
  -e WEBHOOK_FILTER=known \
  -e WEBHOOK_METHOD=GET \
  -e KNOWN_PLATES='{"ABC123":{},"XYZ789":{"owner":"John"}}' \
  --name alpr alpr-ftp

# Watch detected plates
docker logs -f alpr
```

## Configuration

### FTP Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `FTP_USER` | `camera` | FTP username |
| `FTP_PASS` | `camera123` | FTP password |
| `FTP_PORT` | `21` | FTP control port |
| `PASV_MIN` | `21000` | Passive port range start |
| `PASV_MAX` | `21010` | Passive port range end |
| `FTP_DIR` | `/ftp/uploads` | Upload directory |

### Webhook Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_URL` | *(disabled)* | Webhook endpoint URL (embedded auth: `http://user:pass@host/path`) |
| `WEBHOOK_FILTER` | `all` | When to trigger: `all`, `known`, or `unknown` plates |
| `WEBHOOK_METHOD` | `POST` | HTTP method: `GET` (no body) or `POST` (JSON payload) |

### Known Plates Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `KNOWN_PLATES_FILE` | *(none)* | Path to JSON file with known plates |
| `KNOWN_PLATES` | *(none)* | Inline JSON with known plates (file takes precedence) |

## Known Plates Format

JSON object mapping plate numbers to metadata (metadata is optional):

```json
{
  "ABC123": { "owner": "John", "tags": ["family"] },
  "XYZ789": { "owner": "Cleaning Service", "tags": ["service", "weekly"] },
  "DEF456": {}
}
```

Plate matching is case-insensitive.

## Webhook Behavior

### GET Method (Trigger Mode)

For simple devices like Shelly relays that just need an HTTP hit:

```bash
WEBHOOK_URL=http://admin:password@192.168.1.50/relay/0?turn=on
WEBHOOK_METHOD=GET
```

No request body is sent. The URL is simply called.

### POST Method (Webhook Mode)

For smart endpoints that want plate data:

```bash
WEBHOOK_URL=http://homeassistant:8123/api/webhook/alpr
WEBHOOK_METHOD=POST
```

JSON payload:

```json
{
  "plate": "ABC123",
  "confidence": 95.2,
  "timestamp": "2026-02-03T12:34:56+00:00",
  "filename": "Gate_00_20260203123456.jpg",
  "known": true,
  "metadata": { "owner": "John", "tags": ["family"] }
}
```

## Example Configurations

### Gate Opener (Shelly Relay)

Open gate for known plates only:

```bash
docker run -d -p 21:21 -p 21000-21010:21000-21010 \
  -e FTP_USER=camera \
  -e FTP_PASS=secretpass \
  -e WEBHOOK_URL="http://admin:admin@192.168.1.50/relay/0?turn=on" \
  -e WEBHOOK_FILTER=known \
  -e WEBHOOK_METHOD=GET \
  -v /path/to/plates.json:/config/plates.json:ro \
  -e KNOWN_PLATES_FILE=/config/plates.json \
  --name alpr alpr-ftp
```

### Home Assistant Integration

Send all plates to Home Assistant webhook:

```bash
docker run -d -p 21:21 -p 21000-21010:21000-21010 \
  -e FTP_USER=camera \
  -e FTP_PASS=secretpass \
  -e WEBHOOK_URL=http://homeassistant.local:8123/api/webhook/alpr_detected \
  -e WEBHOOK_FILTER=all \
  -e WEBHOOK_METHOD=POST \
  --name alpr alpr-ftp
```

Then in Home Assistant `automations.yaml`:

```yaml
automation:
  - alias: "Gate opener for known plates"
    trigger:
      - platform: webhook
        webhook_id: alpr_detected
    condition:
      - condition: template
        value_template: "{{ trigger.json.known == true }}"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.gate_relay
```

### Alert on Unknown Vehicles

Notify when unknown plates are detected:

```bash
docker run -d -p 21:21 -p 21000-21010:21000-21010 \
  -e FTP_USER=camera \
  -e FTP_PASS=secretpass \
  -e WEBHOOK_URL=http://homeassistant.local:8123/api/webhook/unknown_vehicle \
  -e WEBHOOK_FILTER=unknown \
  -e WEBHOOK_METHOD=POST \
  -e KNOWN_PLATES='{"ABC123":{},"XYZ789":{}}' \
  --name alpr alpr-ftp
```

## Output

Detected plates are logged to stdout:

```
2026-02-03 12:34:56 | PLATE: ABC123 [KNOWN] | conf: 99.9% | file: Gate_00_20260203123456.jpg
2026-02-03 12:34:56 | WEBHOOK: GET http://192.168.1.50/relay/0?turn=on -> 200
2026-02-03 12:35:12 | PLATE: XYZ789 | conf: 95.2% | file: Gate_00_20260203123512.jpg
2026-02-03 12:35:30 | NO PLATE DETECTED | file: Gate_00_20260203123530.jpg
```

## Camera Setup

Configure your IP camera to upload via FTP:

- **Host**: Your Docker host IP
- **Port**: 21 (or your configured `FTP_PORT`)
- **Username**: Value of `FTP_USER`
- **Password**: Value of `FTP_PASS`
- **Path**: `/` (root)
- **Mode**: Passive (PASV)

## Performance

| Metric | Value |
|--------|-------|
| Model load time | ~3s (once at startup) |
| Inference time | ~0.15s per image |
| Memory usage | ~500MB |

## How It Works

```
Camera ──FTP Upload──▶ pyftpdlib ──on_file_received──▶ fast-alpr ──▶ stdout
                                                            │
                                                            ├──▶ Webhook (if configured)
                                                            │
                                                            ├─ YOLO v9 detector
                                                            └─ CCT OCR model
```

## Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run server (non-privileged port)
FTP_PORT=2121 FTP_DIR=/tmp/uploads python alpr_ftp.py

# Upload test image
curl -T image.jpg ftp://camera:camera123@localhost:2121/
```

## Roadmap

- [ ] MQTT support for pub/sub integration

## License

MIT

