#!/usr/bin/env python3
"""
ALPR FTP Server - All-in-one license plate recognition with built-in FTP.

Cameras upload images via FTP, plates are detected automatically and logged to stdout.
Optionally triggers webhooks for automation (gate openers, Home Assistant, etc.).
"""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from fast_alpr import ALPR

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("pyftpdlib").setLevel(logging.WARNING)
logging.getLogger("open_image_models").setLevel(logging.WARNING)

# =============================================================================
# Configuration from environment variables
# =============================================================================

# FTP settings
FTP_USER = os.environ.get("FTP_USER", "camera")
FTP_PASS = os.environ.get("FTP_PASS", "camera123")
FTP_PORT = int(os.environ.get("FTP_PORT", "21"))
PASV_MIN = int(os.environ.get("PASV_MIN", "21000"))
PASV_MAX = int(os.environ.get("PASV_MAX", "21010"))
FTP_DIR = os.environ.get("FTP_DIR", "/ftp/uploads")

# Webhook settings
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # Empty = disabled
WEBHOOK_FILTER = os.environ.get("WEBHOOK_FILTER", "all").lower()  # all | known | unknown
WEBHOOK_METHOD = os.environ.get("WEBHOOK_METHOD", "POST").upper()  # GET | POST

# Known plates settings
KNOWN_PLATES_FILE = os.environ.get("KNOWN_PLATES_FILE", "")
KNOWN_PLATES_ENV = os.environ.get("KNOWN_PLATES", "")

# =============================================================================
# Global state
# =============================================================================

# ALPR instance (loaded once at startup)
alpr: ALPR | None = None

# Known plates dictionary: {"ABC123": {"owner": "John", ...}, ...}
known_plates: dict[str, dict] = {}

# TODO: Add MQTT support for pub/sub integration with Home Assistant, etc.


# =============================================================================
# Known plates loading
# =============================================================================

def load_known_plates() -> dict[str, dict]:
    """
    Load known plates from file or environment variable.
    File takes precedence if both are set.
    
    Returns:
        Dictionary mapping plate numbers to metadata.
        Example: {"ABC123": {"owner": "John"}, "XYZ789": {}}
    """
    plates: dict[str, dict] = {}
    
    # Try loading from file first
    if KNOWN_PLATES_FILE:
        file_path = Path(KNOWN_PLATES_FILE)
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    plates = json.load(f)
                logger.info(f"Loaded {len(plates)} known plates from {KNOWN_PLATES_FILE}")
                return plates
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load known plates from {KNOWN_PLATES_FILE}: {e}")
        else:
            logger.warning(f"Known plates file not found: {KNOWN_PLATES_FILE}")
    
    # Fall back to environment variable
    if KNOWN_PLATES_ENV:
        try:
            plates = json.loads(KNOWN_PLATES_ENV)
            logger.info(f"Loaded {len(plates)} known plates from KNOWN_PLATES env var")
            return plates
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse KNOWN_PLATES env var: {e}")
    
    return plates


# =============================================================================
# Webhook functionality
# =============================================================================

def call_webhook(plate: str, confidence: float, filename: str, is_known: bool, metadata: dict) -> None:
    """
    Call the configured webhook endpoint.
    
    GET method: Simple trigger, no body (for dumb devices like Shelly relays)
    POST method: JSON payload with full plate data (for Home Assistant, etc.)
    """
    if not WEBHOOK_URL:
        return
    
    try:
        if WEBHOOK_METHOD == "GET":
            # Simple trigger - just hit the URL
            req = urllib.request.Request(WEBHOOK_URL, method="GET")
        else:
            # POST with JSON payload
            payload = {
                "plate": plate,
                "confidence": round(confidence, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "filename": filename,
                "known": is_known,
                "metadata": metadata if is_known else {},
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                WEBHOOK_URL,
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            status = response.status
            logger.info(f"WEBHOOK: {WEBHOOK_METHOD} {WEBHOOK_URL} -> {status}")
            
    except urllib.error.HTTPError as e:
        logger.error(f"WEBHOOK ERROR: {WEBHOOK_METHOD} {WEBHOOK_URL} -> {e.code} {e.reason}")
    except urllib.error.URLError as e:
        logger.error(f"WEBHOOK ERROR: {WEBHOOK_METHOD} {WEBHOOK_URL} -> {e.reason}")
    except Exception as e:
        logger.error(f"WEBHOOK ERROR: {e}")


def should_trigger_webhook(plate: str) -> bool:
    """Determine if webhook should be called based on filter setting."""
    if not WEBHOOK_URL:
        return False
    
    is_known = plate.upper() in (p.upper() for p in known_plates.keys())
    
    if WEBHOOK_FILTER == "all":
        return True
    elif WEBHOOK_FILTER == "known":
        return is_known
    elif WEBHOOK_FILTER == "unknown":
        return not is_known
    else:
        logger.warning(f"Invalid WEBHOOK_FILTER '{WEBHOOK_FILTER}', defaulting to 'all'")
        return True


# =============================================================================
# ALPR processing
# =============================================================================

def init_alpr() -> ALPR:
    """Initialize ALPR with optimized settings for CPU inference."""
    logger.info("Loading ALPR models (this takes ~3 seconds)...")
    instance = ALPR(
        detector_model="yolo-v9-t-384-license-plate-end2end",
        ocr_model="cct-xs-v1-global-model",
    )
    logger.info("ALPR models loaded successfully")
    return instance


def process_image(filepath: str) -> None:
    """Process an uploaded image, log detected plates, and trigger webhooks."""
    global alpr
    
    if alpr is None:
        logger.error("ALPR not initialized")
        return
    
    filename = Path(filepath).name
    
    # Skip non-image files
    if not filename.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
        return
    
    try:
        results = alpr.predict(filepath)
        
        if results:
            for result in results:
                plate = result.ocr.text
                confidence = result.ocr.confidence * 100
                
                # Check if plate is known (case-insensitive lookup)
                plate_upper = plate.upper()
                is_known = plate_upper in (p.upper() for p in known_plates.keys())
                metadata = {}
                if is_known:
                    # Get metadata with case-insensitive key lookup
                    for key, value in known_plates.items():
                        if key.upper() == plate_upper:
                            metadata = value
                            break
                
                # Always log to stdout
                known_tag = " [KNOWN]" if is_known else ""
                logger.info(f"PLATE: {plate}{known_tag} | conf: {confidence:.1f}% | file: {filename}")
                
                # Trigger webhook if filter matches
                if should_trigger_webhook(plate):
                    call_webhook(plate, confidence, filename, is_known, metadata)
        else:
            logger.info(f"NO PLATE DETECTED | file: {filename}")
            
    except Exception as e:
        logger.error(f"Error processing {filename}: {e}")


# =============================================================================
# FTP server
# =============================================================================

class ALPRFTPHandler(FTPHandler):
    """Custom FTP handler that processes images after upload completes."""
    
    def on_file_received(self, file: str) -> None:
        """Called when a file upload is complete."""
        process_image(file)


def main() -> None:
    """Start the FTP server with ALPR processing."""
    global alpr, known_plates
    
    # Create upload directory if it doesn't exist
    os.makedirs(FTP_DIR, exist_ok=True)
    
    # Load known plates
    known_plates = load_known_plates()
    
    # Initialize ALPR (loads models into memory)
    alpr = init_alpr()
    
    # Set up FTP authorizer
    authorizer = DummyAuthorizer()
    authorizer.add_user(FTP_USER, FTP_PASS, FTP_DIR, perm="elradfmw")
    
    # Configure FTP handler
    handler = ALPRFTPHandler
    handler.authorizer = authorizer
    handler.passive_ports = range(PASV_MIN, PASV_MAX + 1)
    
    # Create and start FTP server
    server = FTPServer(("0.0.0.0", FTP_PORT), handler)
    server.max_cons = 10
    server.max_cons_per_ip = 5
    
    # Startup logging
    logger.info(f"FTP server starting on port {FTP_PORT}")
    logger.info(f"Passive ports: {PASV_MIN}-{PASV_MAX}")
    logger.info(f"Upload directory: {FTP_DIR}")
    logger.info(f"FTP credentials: {FTP_USER} / {'*' * len(FTP_PASS)}")
    
    if WEBHOOK_URL:
        logger.info(f"Webhook: {WEBHOOK_METHOD} {WEBHOOK_URL} (filter: {WEBHOOK_FILTER})")
    else:
        logger.info("Webhook: disabled")
    
    if known_plates:
        logger.info(f"Known plates: {len(known_plates)} configured")
    else:
        logger.info("Known plates: none configured")
    
    logger.info("Ready to receive images...")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.close_all()


if __name__ == "__main__":
    main()

