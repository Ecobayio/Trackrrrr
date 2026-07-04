"""
Web3 Client - Handles blockchain interactions with BatteryPassport contract
Uses web3.py for signing transactions and reading contract state
"""

import json
import logging
from web3 import Web3
from eth_account import Account
from config import Config

logger = logging.getLogger(__name__)


class Web3Client:
    """Singleton Web3 client for BatteryPassport contract interactions"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Web3Client, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize Web3 connection and contract instance"""
        if self._initialized:
            return

        try:
            # Connect to Polygon Amoy
            self.w3 = Web3(Web3.HTTPProvider(Config.AMOY_RPC_URL))

            if not self.w3.is_connected():
                raise ConnectionError("Failed to connect to Polygon Amoy RPC")

            logger.info(f"✅ Connected to Polygon Amoy")

            # Load contract ABI
            with open(Config.CONTRACT_ABI_PATH, "r") as f:
                contract_data = json.load(f)
                contract_abi = contract_data["abi"]

            # Initialize contract instance
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(Config.CONTRACT_ADDRESS),
                abi=contract_abi,
            )

            # Setup account for signing
            self.account = Account.from_key(Config.METAMASK_PRIVATE_KEY)
            self.deployer_address = Web3.to_checksum_address(Config.DEPLOYER_ADDRESS)

            logger.info(f"✅ Web3 client initialized for {self.deployer_address}")
            self._initialized = True

        except Exception as e:
            logger.error(f"❌ Failed to initialize Web3 client: {str(e)}")
            raise

    # ============================================
    # CORE BLOCKCHAIN FUNCTIONS
    # ============================================

    def mint_battery(self, serial_number: str, metadata_cid: str) -> tuple:
        """
        Mint a new battery NFT on the blockchain
        
        Args:
            serial_number: Raw serial number (will be hashed on-chain)
            metadata_cid: IPFS CID pointing to metadata JSON
            
        Returns:
            (tx_hash, token_id) tuple
            
        Raises:
            Exception on transaction failure
        """
        try:
            logger.info(f"🔄 Minting battery: {serial_number[:20]}... → {metadata_cid}")

            # Validate inputs
            if not serial_number or len(serial_number) == 0:
                raise ValueError("Serial number cannot be empty")
            if not metadata_cid or len(metadata_cid) == 0:
                raise ValueError("Metadata CID cannot be empty")

            # Check gas balance
            balance = self.w3.eth.get_balance(self.deployer_address)
            logger.info(f"💰 Account balance: {Web3.from_wei(balance, 'ether')} MATIC")

            # Estimate gas
            try:
                gas_estimate = self.contract.functions.mintBattery(
                    self.deployer_address,  # to (owner)
                    serial_number,
                    metadata_cid,
                ).estimate_gas({"from": self.deployer_address})

                # Add 20% safety buffer
                gas_with_buffer = int(gas_estimate * Config.GAS_MULTIPLIER)
                logger.info(f"⛽ Estimated gas: {gas_estimate}, with buffer: {gas_with_buffer}")

            except Exception as e:
                logger.warning(f"⚠️  Gas estimation failed: {str(e)}, using fallback")
                gas_with_buffer = 500000  # Fallback estimate

            # Get current gas price
            gas_price = self.w3.eth.gas_price
            logger.info(f"⛽ Current gas price: {Web3.from_wei(gas_price, 'gwei')} gwei")

            # Check if we have enough balance
            tx_cost = gas_with_buffer * gas_price
            if balance < tx_cost:
                raise ValueError(
                    f"Insufficient balance. Have {Web3.from_wei(balance, 'ether')} MATIC, "
                    f"need {Web3.from_wei(tx_cost, 'ether')} MATIC"
                )

            # Build transaction
            tx_dict = self.contract.functions.mintBattery(
                self.deployer_address,  # to (owner)
                serial_number,
                metadata_cid,
            ).build_transaction(
                {
                    "from": self.deployer_address,
                    "gas": gas_with_buffer,
                    "gasPrice": gas_price,
                    "nonce": self.w3.eth.get_transaction_count(self.deployer_address),
                }
            )

            # Sign transaction
            signed_tx = self.w3.eth.account.sign_transaction(tx_dict, Config.METAMASK_PRIVATE_KEY)
            logger.info(f"✅ Transaction signed")

            # Send transaction
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_hex = tx_hash.hex()
            logger.info(f"✅ Transaction sent: {tx_hash_hex}")

            # Wait for receipt (with timeout)
            try:
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                logger.info(f"✅ Transaction confirmed in block {receipt['blockNumber']}")

                # Parse token ID from events
                token_id = self._parse_token_id_from_receipt(receipt)
                logger.info(f"✅ Minted token ID: {token_id}")

                return tx_hash_hex, token_id

            except TimeoutError:
                logger.warning(f"⚠️  Transaction confirmation timeout, returning tx_hash: {tx_hash_hex}")
                # Return None for token_id until confirmed (caller will retry)
                return tx_hash_hex, None

        except Exception as e:
            logger.error(f"❌ Mint failed: {str(e)}")
            raise

    def verify_battery(
        self, token_id: int, confidence_score: int, photo_hash: str
    ) -> str:
        """
        Verify a battery (VERIFIER_ROLE only)
        
        Args:
            token_id: Token ID to verify
            confidence_score: AI confidence score (0-100)
            photo_hash: IPFS hash of verification photo
            
        Returns:
            tx_hash of verification transaction
        """
        try:
            logger.info(f"🔄 Verifying battery token {token_id}, confidence: {confidence_score}%")

            # Validate inputs
            if confidence_score < 0 or confidence_score > 100:
                raise ValueError("Confidence score must be 0-100")
            if not photo_hash or len(photo_hash) == 0:
                raise ValueError("Photo hash cannot be empty")

            # Build and send transaction
            tx_dict = self.contract.functions.verifyBattery(
                token_id, confidence_score, photo_hash
            ).build_transaction(
                {
                    "from": self.deployer_address,
                    "gas": 300000,
                    "gasPrice": self.w3.eth.gas_price,
                    "nonce": self.w3.eth.get_transaction_count(self.deployer_address),
                }
            )

            signed_tx = self.w3.eth.account.sign_transaction(tx_dict, Config.METAMASK_PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)

            logger.info(f"✅ Verification transaction sent: {tx_hash.hex()}")
            return tx_hash.hex()

        except Exception as e:
            logger.error(f"❌ Verification failed: {str(e)}")
            raise

    # ============================================
    # READ-ONLY FUNCTIONS
    # ============================================

    def get_battery_info(self, token_id: int) -> tuple:
        """
        Get battery information by token ID
        
        Returns:
            (owner_address, metadata_cid, serial_hash, mint_timestamp)
        """
        try:
            owner = self.contract.functions.ownerOf(token_id).call()
            metadata_cid = self.contract.functions.getMetadataCID(token_id).call()
            serial_hash = self.contract.functions.getSerialHash(token_id).call()
            mint_timestamp = self.contract.functions.getMintTimestamp(token_id).call()

            return owner, metadata_cid, serial_hash, mint_timestamp

        except Exception as e:
            logger.error(f"❌ Failed to get battery info: {str(e)}")
            return None, None, None, None

    def get_total_minted(self) -> int:
        """Get total batteries minted"""
        try:
            return self.contract.functions.getTotalMinted().call()
        except Exception as e:
            logger.error(f"❌ Failed to get total minted: {str(e)}")
            return 0

    def is_verified(self, token_id: int) -> bool:
        """Check if battery is verified"""
        try:
            return self.contract.functions.isVerified(token_id).call()
        except Exception as e:
            logger.error(f"❌ Failed to check verification status: {str(e)}")
            return False

    def verify_serial(self, token_id: int, serial_number: str) -> bool:
        """Verify a serial number matches a token"""
        try:
            return self.contract.functions.verifySerial(token_id, serial_number).call()
        except Exception as e:
            logger.error(f"❌ Failed to verify serial: {str(e)}")
            return False

    # ============================================
    # HELPER FUNCTIONS
    # ============================================

    def _parse_token_id_from_receipt(self, receipt) -> int:
        """Extract token ID from BatteryMinted event in receipt"""
        try:
            # Parse logs for BatteryMinted event
            logs = self.contract.events.BatteryMinted().process_receipt(receipt)
            if logs:
                return logs[0]["args"]["tokenId"]
            return None
        except Exception as e:
            logger.warning(f"⚠️  Could not parse token ID from receipt: {str(e)}")
            return None

    def get_account_balance(self) -> float:
        """Get deployer account balance in MATIC"""
        try:
            balance = self.w3.eth.get_balance(self.deployer_address)
            return float(Web3.from_wei(balance, "ether"))
        except Exception as e:
            logger.error(f"❌ Failed to get balance: {str(e)}")
            return 0.0


# Singleton instance
_web3_client = None


def get_web3_client():
    """Get or create Web3 client singleton"""
    global _web3_client
    if _web3_client is None:
        _web3_client = Web3Client()
    return _web3_client
