"""
Pinata Client - Handles IPFS uploads and downloads via Pinata gateway
"""

import json
import logging
import requests
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)


class PinataClient:
    """Client for uploading and retrieving data from IPFS via Pinata"""

    def __init__(self):
        """Initialize Pinata client with API credentials"""
        self.api_key = Config.PINATA_API_KEY
        self.secret_key = Config.PINATA_SECRET_KEY
        self.gateway = Config.PINATA_GATEWAY

        self.upload_url = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
        self.headers = {
            "pinata_api_key": self.api_key,
            "pinata_secret_api_key": self.secret_key,
            "Content-Type": "application/json",
        }

        logger.info(f"✅ Pinata client initialized")

    # ============================================
    # UPLOAD FUNCTIONS
    # ============================================

    def upload_json(self, data: dict, filename: str = "data.json") -> Optional[str]:
        """
        Upload JSON data to IPFS via Pinata
        
        Args:
            data: Dictionary to upload
            filename: Name for the file on IPFS
            
        Returns:
            IPFS CID (hash) on success, None on failure
        """
        try:
            logger.info(f"📤 Uploading JSON to IPFS: {filename}")

            payload = {
                "pinataContent": data,
                "pinataMetadata": {
                    "name": filename,
                    "keyvalues": {
                        "type": "battery_metadata",
                        "timestamp": str(__import__("datetime").datetime.now().isoformat()),
                    },
                },
            }

            response = requests.post(
                self.upload_url,
                headers=self.headers,
                json=payload,
                timeout=30,
            )

            if response.status_code == 200:
                cid = response.json()["IpfsHash"]
                logger.info(f"✅ Uploaded to IPFS: {cid}")
                return cid
            else:
                logger.error(
                    f"❌ Pinata upload failed: {response.status_code} - {response.text}"
                )
                return None

        except requests.exceptions.Timeout:
            logger.error("❌ Pinata upload timeout (30s)")
            return None
        except Exception as e:
            logger.error(f"❌ Failed to upload JSON to IPFS: {str(e)}")
            return None

    def upload_file(self, file_path: str, filename: str = None) -> Optional[str]:
        """
        Upload a file to IPFS via Pinata
        
        Args:
            file_path: Local path to file
            filename: Name for the file on IPFS (defaults to basename)
            
        Returns:
            IPFS CID on success, None on failure
        """
        try:
            import os

            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")

            if not filename:
                filename = os.path.basename(file_path)

            logger.info(f"📤 Uploading file to IPFS: {filename}")

            with open(file_path, "rb") as f:
                files = {
                    "file": (filename, f, "application/octet-stream"),
                    "pinataMetadata": (
                        None,
                        json.dumps({
                            "name": filename,
                            "keyvalues": {
                                "type": "battery_photo",
                                "timestamp": str(__import__("datetime").datetime.now().isoformat()),
                            },
                        }),
                        "application/json",
                    ),
                }

                headers_file = {
                    "pinata_api_key": self.api_key,
                    "pinata_secret_api_key": self.secret_key,
                }

                response = requests.post(
                    "https://api.pinata.cloud/pinning/pinFileToIPFS",
                    headers=headers_file,
                    files=files,
                    timeout=60,
                )

            if response.status_code == 200:
                cid = response.json()["IpfsHash"]
                logger.info(f"✅ File uploaded to IPFS: {cid}")
                return cid
            else:
                logger.error(
                    f"❌ Pinata file upload failed: {response.status_code} - {response.text}"
                )
                return None

        except FileNotFoundError as e:
            logger.error(f"❌ {str(e)}")
            return None
        except requests.exceptions.Timeout:
            logger.error("❌ Pinata file upload timeout (60s)")
            return None
        except Exception as e:
            logger.error(f"❌ Failed to upload file to IPFS: {str(e)}")
            return None

    # ============================================
    # RETRIEVE FUNCTIONS
    # ============================================

    def get_json(self, cid: str) -> Optional[dict]:
        """
        Retrieve JSON data from IPFS
        
        Args:
            cid: IPFS content hash
            
        Returns:
            Parsed JSON object on success, None on failure
        """
        try:
            url = f"{self.gateway}/ipfs/{cid}"
            logger.info(f"📥 Retrieving JSON from IPFS: {cid}")

            response = requests.get(url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                logger.info(f"✅ Retrieved JSON from IPFS")
                return data
            else:
                logger.error(f"❌ IPFS retrieval failed: {response.status_code}")
                return None

        except requests.exceptions.Timeout:
            logger.error("❌ IPFS retrieval timeout (10s)")
            return None
        except json.JSONDecodeError:
            logger.error("❌ Invalid JSON from IPFS")
            return None
        except Exception as e:
            logger.error(f"❌ Failed to retrieve JSON from IPFS: {str(e)}")
            return None

    def get_file_url(self, cid: str) -> str:
        """
        Get a direct URL to retrieve a file from IPFS
        
        Args:
            cid: IPFS content hash
            
        Returns:
            Full URL to access the file
        """
        return f"{self.gateway}/ipfs/{cid}"

    # ============================================
    # UTILITY FUNCTIONS
    # ============================================

    def create_metadata_json(
        self,
        serial_number: str,
        photo_cid: str,
        manufacturer: str = None,
        model: str = None,
        capacity_wh: int = None,
        chemistry: str = None,
        specs: dict = None,
        **kwargs,
    ) -> dict:
        """
        Create a standardized battery metadata JSON
        
        Args:
            serial_number: Battery serial number
            photo_cid: IPFS CID of verification photo
            manufacturer: Battery manufacturer
            model: Battery model
            capacity_wh: Capacity in watt-hours
            chemistry: Battery chemistry (e.g., LFP, NCA)
            specs: Additional technical specifications
            **kwargs: Additional metadata fields
            
        Returns:
            Metadata dictionary ready for JSON upload
        """
        from datetime import datetime

        metadata = {
            "version": "1.0",
            "serialNumber": serial_number,
            "photoCid": photo_cid,
            "manufacturer": manufacturer,
            "model": model,
            "capacityWh": capacity_wh,
            "chemistry": chemistry,
            "specs": specs or {},
            "createdAt": datetime.now().isoformat(),
            "compliance": {
                "standard": "EU Battery Regulation (2023/1542)",
                "digitalTwin": True,
            },
        }

        # Merge additional fields
        metadata.update(kwargs)

        return metadata


# Singleton instance
_pinata_client = None


def get_pinata_client():
    """Get or create Pinata client singleton"""
    global _pinata_client
    if _pinata_client is None:
        _pinata_client = PinataClient()
    return _pinata_client
