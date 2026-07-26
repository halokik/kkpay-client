"""Direct, self-hosted TRON payment verification.

This module talks to a TRON full-node/TronGrid-compatible HTTP endpoint
directly.  It deliberately has no dependency on a KKPay gateway, merchant
ID, API token, receiving-address pool, or remote checkout service.

Only public chain data is queried here.  Receiving private keys must stay in
the operator's own wallet and are never accepted or stored by this package.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .errors import KKPayChainError, KKPayConfigurationError
from .models import RetryPolicy, TradeType


DEFAULT_TRON_API_URL = "https://api.trongrid.io"
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRC20_TRANSFER_EVENT_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TRON_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_TRON_BASE58_INDEX = {character: index for index, character in enumerate(TRON_BASE58_ALPHABET)}
_SUN = Decimal(10**6)


def _base58check_encode(payload: bytes) -> str:
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    raw = payload + checksum
    number = int.from_bytes(raw, "big")
    characters: list[str] = []
    while number:
        number, remainder = divmod(number, 58)
        characters.append(TRON_BASE58_ALPHABET[remainder])
    prefix = "1" * (len(raw) - len(raw.lstrip(b"\0")))
    return prefix + "".join(reversed(characters or ["1"]))


def _base58check_decode(address: str) -> bytes:
    value = str(address or "").strip()
    if not value:
        raise ValueError("TRON address must not be empty")
    number = 0
    for character in value:
        try:
            digit = _TRON_BASE58_INDEX[character]
        except KeyError as exc:
            raise ValueError("TRON address contains a non-base58 character") from exc
        number = number * 58 + digit
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    raw = b"\0" * (len(value) - len(value.lstrip("1"))) + raw
    if len(raw) != 25:
        raise ValueError("TRON address has an invalid length")
    payload, checksum = raw[:-4], raw[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise ValueError("TRON address checksum is invalid")
    if len(payload) != 21 or payload[0] != 0x41:
        raise ValueError("TRON address is not a mainnet base58 address")
    return payload


def is_valid_tron_address(address: object) -> bool:
    """Return whether *address* is a checksum-valid mainnet T-address."""

    value = str(address or "").strip()
    if len(value) != 34 or not value.startswith("T"):
        return False
    try:
        _base58check_decode(value)
    except ValueError:
        return False
    return True


def tron_address_to_hex(address: object) -> str:
    """Return a mainnet address as lowercase ``41``-prefixed hexadecimal."""

    value = str(address or "").strip()
    if not is_valid_tron_address(value):
        raise ValueError("invalid TRON receiving address")
    return _base58check_decode(value).hex()


def normalize_tron_address(address: object) -> str:
    """Normalize a base58, ``41``-prefixed, or EVM-style TRON address.

    TronGrid transaction logs conventionally omit the ``41`` network byte.
    Normalizing all forms before comparison prevents a valid incoming transfer
    from being rejected merely because two endpoints serialize the address
    differently.
    """

    value = str(address or "").strip()
    if is_valid_tron_address(value):
        return value
    raw = value.lower().removeprefix("0x")
    if len(raw) == 40 and all(character in "0123456789abcdef" for character in raw):
        raw = "41" + raw
    if len(raw) == 42 and raw.startswith("41") and all(
        character in "0123456789abcdef" for character in raw
    ):
        return _base58check_encode(bytes.fromhex(raw))
    return value


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise KKPayChainError(f"invalid {field} in chain response") from exc
    if not amount.is_finite() or amount < 0:
        raise KKPayChainError(f"invalid {field} in chain response")
    return amount


def _same_hash(left: object, right: object) -> bool:
    return str(left or "").strip().lower() == str(right or "").strip().lower()


@dataclass(frozen=True)
class ChainTransfer:
    """One confirmed inbound TRON transfer discovered from public chain data."""

    tx_hash: str
    amount: Decimal
    timestamp_ms: int
    sender: str
    recipient: str
    trade_type: str


class _TronClientBase:
    """Shared validation and response parsing for sync and async clients."""

    def __init__(
        self,
        api_url: str = DEFAULT_TRON_API_URL,
        *,
        api_key: str | None = None,
        usdt_contract: str = USDT_TRC20_CONTRACT,
        timeout: float = 15.0,
        allow_insecure_http: bool = False,
        retry_policy: RetryPolicy | None = None,
        max_pages: int = 10,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_url = str(api_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.usdt_contract = normalize_tron_address(usdt_contract)
        self.timeout = float(timeout)
        self.retry_policy = retry_policy or RetryPolicy()
        self.max_pages = int(max_pages)
        self._transport = transport

        parsed = urlparse(self.api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise KKPayConfigurationError("tron api_url must be an absolute HTTP(S) URL")
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme == "http" and parsed.hostname not in local_hosts and not allow_insecure_http:
            raise KKPayConfigurationError(
                "public HTTP TRON endpoint refused; use HTTPS or explicitly set "
                "allow_insecure_http=True"
            )
        if not is_valid_tron_address(self.usdt_contract):
            raise KKPayConfigurationError("usdt_contract must be a valid TRON address")
        if self.timeout <= 0:
            raise KKPayConfigurationError("TRON timeout must be positive")
        if not 1 <= self.max_pages <= 100:
            raise KKPayConfigurationError("max_pages must be between 1 and 100")

    def __repr__(self) -> str:
        key = "***" if self.api_key else ""
        return (
            f"{type(self).__name__}(api_url={self.api_url!r}, "
            f"usdt_contract={self.usdt_contract!r}, api_key={key!r})"
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {"TRON-PRO-API-KEY": self.api_key} if self.api_key else {}

    def _url(self, path: str) -> str:
        return f"{self.api_url}{path}"

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise KKPayChainError(f"TRON endpoint returned HTTP {exc.response.status_code}") from exc
        except (ValueError, httpx.HTTPError) as exc:
            raise KKPayChainError(f"invalid TRON endpoint response: {exc}") from exc
        if not isinstance(data, dict):
            raise KKPayChainError("TRON endpoint response must be a JSON object")
        if data.get("success") is False:
            message = str(data.get("Error") or data.get("message") or "TRON endpoint rejected request")
            raise KKPayChainError(message)
        return data

    @staticmethod
    def _is_success_receipt(info: dict[str, Any]) -> bool:
        receipt = info.get("receipt") or {}
        return str(receipt.get("result") or "").upper() == "SUCCESS"

    @staticmethod
    def _is_success_transaction(transaction: dict[str, Any]) -> bool:
        results = transaction.get("ret") or []
        if not results:
            return False
        return str((results[0] or {}).get("contractRet") or "").upper() == "SUCCESS"

    @staticmethod
    def _in_payment_window(
        timestamp_ms: object,
        *,
        created_at_ms: int,
        expires_at_ms: int | None,
    ) -> bool:
        try:
            timestamp = int(timestamp_ms)
        except (TypeError, ValueError):
            return False
        if timestamp < created_at_ms:
            return False
        return expires_at_ms is None or timestamp <= expires_at_ms

    @staticmethod
    def _checked_trade_type(trade_type: object) -> str:
        value = str(trade_type or "").strip()
        if value not in {TradeType.USDT_TRC20, TradeType.TRX}:
            raise ValueError("trade_type must be 'usdt.trc20' or 'tron.trx'")
        return value

    def _trc20_transfers(self, payload: dict[str, Any], address: str) -> list[ChainTransfer]:
        result: list[ChainTransfer] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            token_info = item.get("token_info") or {}
            if not isinstance(token_info, dict):
                continue
            if normalize_tron_address(token_info.get("address")) != self.usdt_contract:
                continue
            if str(item.get("type") or "") != "Transfer":
                continue
            recipient = normalize_tron_address(item.get("to"))
            if recipient != address:
                continue
            tx_hash = str(item.get("transaction_id") or "").strip()
            if not tx_hash:
                continue
            try:
                amount = Decimal(int(str(item.get("value") or ""))) / _SUN
                timestamp = int(item.get("block_timestamp") or 0)
            except (InvalidOperation, TypeError, ValueError):
                continue
            if amount <= 0 or timestamp <= 0:
                continue
            result.append(
                ChainTransfer(
                    tx_hash=tx_hash,
                    amount=amount,
                    timestamp_ms=timestamp,
                    sender=normalize_tron_address(item.get("from")),
                    recipient=recipient,
                    trade_type=TradeType.USDT_TRC20,
                )
            )
        return result

    def _trx_transfers(self, payload: dict[str, Any], address: str) -> list[ChainTransfer]:
        result: list[ChainTransfer] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict) or not self._is_success_transaction(item):
                continue
            tx_hash = str(item.get("txID") or "").strip()
            timestamp = int(item.get("block_timestamp") or 0)
            if not tx_hash or timestamp <= 0:
                continue
            for contract in ((item.get("raw_data") or {}).get("contract") or []):
                if not isinstance(contract, dict) or contract.get("type") != "TransferContract":
                    continue
                value = ((contract.get("parameter") or {}).get("value") or {})
                recipient = normalize_tron_address(value.get("to_address"))
                if recipient != address:
                    continue
                try:
                    amount = Decimal(int(str(value.get("amount") or ""))) / _SUN
                except (InvalidOperation, TypeError, ValueError):
                    continue
                if amount <= 0:
                    continue
                result.append(
                    ChainTransfer(
                        tx_hash=tx_hash,
                        amount=amount,
                        timestamp_ms=timestamp,
                        sender=normalize_tron_address(value.get("owner_address")),
                        recipient=recipient,
                        trade_type=TradeType.TRX,
                    )
                )
        return result

    @staticmethod
    def _next_fingerprint(payload: dict[str, Any]) -> str:
        meta = payload.get("meta") or {}
        if not isinstance(meta, dict):
            return ""
        return str(meta.get("fingerprint") or "").strip()

    def _verify_usdt_info(
        self,
        info: dict[str, Any],
        transfer: ChainTransfer,
        *,
        address: str,
        amount: Decimal,
        created_at_ms: int,
        expires_at_ms: int | None,
    ) -> bool:
        if not _same_hash(info.get("id"), transfer.tx_hash):
            return False
        if not self._is_success_receipt(info):
            return False
        if not self._in_payment_window(
            info.get("blockTimeStamp"),
            created_at_ms=created_at_ms,
            expires_at_ms=expires_at_ms,
        ):
            return False
        for event in info.get("log") or []:
            if not isinstance(event, dict):
                continue
            topics = event.get("topics") or []
            if len(topics) < 3:
                continue
            topic = str(topics[0] or "").lower().removeprefix("0x")
            if topic != TRC20_TRANSFER_EVENT_TOPIC:
                continue
            if normalize_tron_address(event.get("address")) != self.usdt_contract:
                continue
            recipient = normalize_tron_address(str(topics[2] or "")[-40:])
            if recipient != address:
                continue
            raw_amount = str(event.get("data") or "").lower().removeprefix("0x")
            try:
                event_amount = Decimal(int(raw_amount, 16)) / _SUN
            except (TypeError, ValueError):
                continue
            if event_amount == amount:
                return True
        return False

    def _verify_trx_data(
        self,
        info: dict[str, Any],
        transaction: dict[str, Any],
        transfer: ChainTransfer,
        *,
        address: str,
        amount: Decimal,
        created_at_ms: int,
        expires_at_ms: int | None,
    ) -> bool:
        if not _same_hash(transaction.get("txID"), transfer.tx_hash):
            return False
        if info.get("id") and not _same_hash(info.get("id"), transfer.tx_hash):
            return False
        if not self._is_success_transaction(transaction):
            return False
        if not self._in_payment_window(
            info.get("blockTimeStamp"),
            created_at_ms=created_at_ms,
            expires_at_ms=expires_at_ms,
        ):
            return False
        for contract in ((transaction.get("raw_data") or {}).get("contract") or []):
            if not isinstance(contract, dict) or contract.get("type") != "TransferContract":
                continue
            value = ((contract.get("parameter") or {}).get("value") or {})
            if normalize_tron_address(value.get("to_address")) != address:
                continue
            try:
                transfer_amount = Decimal(int(str(value.get("amount") or ""))) / _SUN
            except (InvalidOperation, TypeError, ValueError):
                continue
            if transfer_amount == amount:
                return True
        return False


class TronClient(_TronClientBase):
    """Blocking direct TRON client for a self-hosted payment application.

    ``api_url`` defaults to TronGrid but can point to the recipient's own
    full-node proxy.  The SDK never contacts the package author's server.
    """

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: httpx.HTTPError | None = None
        for attempt in range(1, self.retry_policy.attempts + 1):
            try:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    response = client.request(method, self._url(path), headers=self._headers, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.retry_policy.attempts:
                    break
            else:
                if response.status_code not in self.retry_policy.status_codes or attempt >= self.retry_policy.attempts:
                    return self._json(response)
            time.sleep(self.retry_policy.delay(attempt))
        raise KKPayChainError(
            f"TRON endpoint request failed after {self.retry_policy.attempts} attempts"
        ) from last_error

    def list_transfers(
        self,
        address: str,
        *,
        trade_type: str = TradeType.USDT_TRC20,
        min_timestamp_ms: int = 0,
    ) -> list[ChainTransfer]:
        """List confirmed inbound USDT-TRC20 or TRX transfers for an address."""

        address = normalize_tron_address(address)
        if not is_valid_tron_address(address):
            raise ValueError("address must be a valid TRON receiving address")
        trade_type = self._checked_trade_type(trade_type)
        min_timestamp = max(0, int(min_timestamp_ms))
        if trade_type == TradeType.USDT_TRC20:
            path = f"/v1/accounts/{quote(address, safe='')}/transactions/trc20"
            params: dict[str, Any] = {
                "only_to": "true",
                "only_confirmed": "true",
                "contract_address": self.usdt_contract,
                "min_timestamp": min_timestamp,
                "limit": 200,
            }
            parser = self._trc20_transfers
        else:
            path = f"/v1/accounts/{quote(address, safe='')}/transactions"
            params = {
                "only_to": "true",
                "only_confirmed": "true",
                "min_timestamp": min_timestamp,
                "limit": 200,
            }
            parser = self._trx_transfers

        transfers: list[ChainTransfer] = []
        fingerprint = ""
        for _ in range(self.max_pages):
            page_params = dict(params)
            if fingerprint:
                page_params["fingerprint"] = fingerprint
            payload = self._request("GET", path, params=page_params)
            page = parser(payload, address)
            transfers.extend(page)
            next_fingerprint = self._next_fingerprint(payload)
            if not next_fingerprint or next_fingerprint == fingerprint:
                break
            if page and min(item.timestamp_ms for item in page) <= min_timestamp:
                break
            fingerprint = next_fingerprint
        return sorted(transfers, key=lambda item: (item.timestamp_ms, item.tx_hash))

    def verify_transfer(
        self,
        transfer: ChainTransfer,
        *,
        address: str,
        amount: Decimal | str | int | float,
        created_at_ms: int,
        expires_at_ms: int | None,
    ) -> bool:
        """Re-read a confirmed transaction and verify its exact payment fields."""

        address = normalize_tron_address(address)
        if not is_valid_tron_address(address):
            raise ValueError("address must be a valid TRON receiving address")
        expected_amount = _decimal(amount, field="expected payment amount")
        trade_type = self._checked_trade_type(transfer.trade_type)
        if transfer.recipient != address or transfer.amount != expected_amount:
            return False
        if not self._in_payment_window(
            transfer.timestamp_ms,
            created_at_ms=int(created_at_ms),
            expires_at_ms=expires_at_ms,
        ):
            return False
        info = self._request(
            "POST",
            "/walletsolidity/gettransactioninfobyid",
            json={"value": transfer.tx_hash},
        )
        if trade_type == TradeType.USDT_TRC20:
            return self._verify_usdt_info(
                info,
                transfer,
                address=address,
                amount=expected_amount,
                created_at_ms=int(created_at_ms),
                expires_at_ms=expires_at_ms,
            )
        transaction = self._request(
            "POST",
            "/walletsolidity/gettransactionbyid",
            json={"value": transfer.tx_hash},
        )
        return self._verify_trx_data(
            info,
            transaction,
            transfer,
            address=address,
            amount=expected_amount,
            created_at_ms=int(created_at_ms),
            expires_at_ms=expires_at_ms,
        )


class AsyncTronClient(_TronClientBase):
    """Asyncio-native direct TRON client for Telethon/ASGI applications."""

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: httpx.HTTPError | None = None
        for attempt in range(1, self.retry_policy.attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                    response = await client.request(
                        method, self._url(path), headers=self._headers, **kwargs
                    )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.retry_policy.attempts:
                    break
            else:
                if response.status_code not in self.retry_policy.status_codes or attempt >= self.retry_policy.attempts:
                    return self._json(response)
            await asyncio.sleep(self.retry_policy.delay(attempt))
        raise KKPayChainError(
            f"TRON endpoint request failed after {self.retry_policy.attempts} attempts"
        ) from last_error

    async def list_transfers(
        self,
        address: str,
        *,
        trade_type: str = TradeType.USDT_TRC20,
        min_timestamp_ms: int = 0,
    ) -> list[ChainTransfer]:
        address = normalize_tron_address(address)
        if not is_valid_tron_address(address):
            raise ValueError("address must be a valid TRON receiving address")
        trade_type = self._checked_trade_type(trade_type)
        min_timestamp = max(0, int(min_timestamp_ms))
        if trade_type == TradeType.USDT_TRC20:
            path = f"/v1/accounts/{quote(address, safe='')}/transactions/trc20"
            params: dict[str, Any] = {
                "only_to": "true",
                "only_confirmed": "true",
                "contract_address": self.usdt_contract,
                "min_timestamp": min_timestamp,
                "limit": 200,
            }
            parser = self._trc20_transfers
        else:
            path = f"/v1/accounts/{quote(address, safe='')}/transactions"
            params = {
                "only_to": "true",
                "only_confirmed": "true",
                "min_timestamp": min_timestamp,
                "limit": 200,
            }
            parser = self._trx_transfers

        transfers: list[ChainTransfer] = []
        fingerprint = ""
        for _ in range(self.max_pages):
            page_params = dict(params)
            if fingerprint:
                page_params["fingerprint"] = fingerprint
            payload = await self._request("GET", path, params=page_params)
            page = parser(payload, address)
            transfers.extend(page)
            next_fingerprint = self._next_fingerprint(payload)
            if not next_fingerprint or next_fingerprint == fingerprint:
                break
            if page and min(item.timestamp_ms for item in page) <= min_timestamp:
                break
            fingerprint = next_fingerprint
        return sorted(transfers, key=lambda item: (item.timestamp_ms, item.tx_hash))

    async def verify_transfer(
        self,
        transfer: ChainTransfer,
        *,
        address: str,
        amount: Decimal | str | int | float,
        created_at_ms: int,
        expires_at_ms: int | None,
    ) -> bool:
        address = normalize_tron_address(address)
        if not is_valid_tron_address(address):
            raise ValueError("address must be a valid TRON receiving address")
        expected_amount = _decimal(amount, field="expected payment amount")
        trade_type = self._checked_trade_type(transfer.trade_type)
        if transfer.recipient != address or transfer.amount != expected_amount:
            return False
        if not self._in_payment_window(
            transfer.timestamp_ms,
            created_at_ms=int(created_at_ms),
            expires_at_ms=expires_at_ms,
        ):
            return False
        info = await self._request(
            "POST",
            "/walletsolidity/gettransactioninfobyid",
            json={"value": transfer.tx_hash},
        )
        if trade_type == TradeType.USDT_TRC20:
            return self._verify_usdt_info(
                info,
                transfer,
                address=address,
                amount=expected_amount,
                created_at_ms=int(created_at_ms),
                expires_at_ms=expires_at_ms,
            )
        transaction = await self._request(
            "POST",
            "/walletsolidity/gettransactionbyid",
            json={"value": transfer.tx_hash},
        )
        return self._verify_trx_data(
            info,
            transaction,
            transfer,
            address=address,
            amount=expected_amount,
            created_at_ms=int(created_at_ms),
            expires_at_ms=expires_at_ms,
        )
