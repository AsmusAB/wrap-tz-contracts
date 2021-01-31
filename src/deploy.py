import json
from pathlib import Path
from typing import TypedDict

from pytezos import Contract

from src.ligo import LigoContract, LigoView, PtzUtils
from src.token import Token
from src.minter import Minter


def _print_contract(addr):
    print(
        f'Successfully originated {addr}\n'
        f'Check out the contract at https://you.better-call.dev/delphinet/{addr}')


class TokenType(TypedDict):
    eth_contract: str
    eth_symbol: str
    symbol: str
    name: str
    decimals: int


def _metadata_encode(content):
    meta_content = json.dumps(content, indent=2).encode().hex()
    meta_uri = str.encode("tezos-storage:content").hex()
    return {"": meta_uri, "content": meta_content}


class Deploy(object):

    def __init__(self, client: PtzUtils):
        self.utils = client
        self.minter_contract = LigoContract("./ligo/minter/main.religo", "main").get_contract().contract
        self.quorum_contract = LigoContract("./ligo/quorum/multisig.religo", "main").get_contract().contract
        root_dir = Path(__file__).parent.parent / "michelson"
        self.fa2_contract = Contract.from_file(root_dir / "fa2.tz")

    def run(self, signers: dict[str, str], tokens: list[TokenType], threshold=1):
        fa2 = self.fa2(tokens)
        quorum = self._deploy_quorum(signers, threshold)
        minter = self._deploy_minter(quorum, tokens, fa2)
        self._set_fa2_admin(minter, fa2)
        self._confirm_admin(minter, fa2)
        print(f"FA2 contract: {fa2}\nQuorum contract: {quorum}\nMinter contract: {minter}")

    def _confirm_admin(self, minter, fa2_contract):
        Minter(self.utils).confirm_admin(minter, fa2_contract)

    def _set_fa2_admin(self, minter, fa2):
        Token(self.utils).set_admin(fa2, minter)

    def fa2(self, tokens: list[TokenType]):
        print("Deploying fa2")
        views = LigoView("./ligo/fa2/views.religo")
        get_balance = views.compile("get_balance", "nat", "get_balance as defined in tzip-12")
        total_supply = views.compile("total_supply", "nat", "get_total supply as defined in tzip-12")
        is_operator = views.compile("is_operator", "bool", "is_operator as defined in tzip-12")
        token_metadata = views.compile("token_metadata", "(pair nat (map string bytes))",
                                       "is_operator as defined in tzip-12")
        meta = _metadata_encode({
            "interfaces": ["TZIP-12", "TZIP-16"],
            "name": "Wrap protocol FA2 tokens",
            "homepage": "https://github.com/bender-labs/wrap-tz-contracts",
            "license": {"name": "MIT"},
            "permissions": {
                "operator": "owner-or-operator-transfer",
                "receiver": "owner-no-hook",
                "sender": "owner-no-hook",
                "custom": {"tag": "PAUSABLE_TOKENS"},
            },
            "views": [
                get_balance,
                total_supply,
                is_operator,
                token_metadata
            ]
        })

        token_metadata = dict(
            [(k, {'token_id': k,
                  'extras': {'decimals': str(v['decimals']).encode().hex(),
                             'eth_contract': v['eth_contract'].encode().hex(),
                             'eth_symbol': v['eth_symbol'].encode().hex(),
                             'name': v['name'].encode().hex(),
                             'symbol': v['symbol'].encode().hex()
                             }}) for k, v in
             enumerate(tokens)])
        supply = dict([(k, 0) for k, v in enumerate(tokens)])
        initial_storage = self.fa2_contract.storage.encode({
            'admin': {
                'admin': self.utils.client.key.public_key_hash(),
                'pending_admin': None,
                'paused': {}
            },
            'assets': {
                'ledger': {},
                'operators': {},
                'token_metadata': token_metadata,
                'token_total_supply': supply
            },
            'metadata': meta
        })
        contract_id = self.utils.originate(self.fa2_contract.code, initial_storage)
        _print_contract(contract_id)
        return contract_id

    def _deploy_minter(self, quorum_contract, tokens: list[TokenType], fa2_contract):
        print("Deploying minter contract")
        token_metadata = dict((v["eth_contract"][2:], [fa2_contract, k]) for k, v in enumerate(tokens))
        metadata = _metadata_encode({
            "name": "Wrap protocol minter contract",
            "homepage": "https://github.com/bender-labs/wrap-tz-contracts",
            "license": {"name": "MIT"},
        })
        initial_storage = self.minter_contract.storage.encode({
            "admin": {
                "administrator": self.utils.client.key.public_key_hash(),
                "signer": quorum_contract,
                "paused": False
            },
            "assets": {
                "tokens": token_metadata,
                "mints": {}
            },
            "governance": {
                "contract": self.utils.client.key.public_key_hash(),
                "fees_contract": self.utils.client.key.public_key_hash(),
                "wrapping_fees": 100,
                "unwrapping_fees": 100,
            },
            "metadata": metadata
        })

        contract_id = self.utils.originate(self.minter_contract.code, initial_storage)
        _print_contract(contract_id)
        return contract_id

    def _deploy_quorum(self, signers: dict[str, str], threshold):
        metadata = _metadata_encode({
            "name": "Wrap protocol quorum contract",
            "homepage": "https://github.com/bender-labs/wrap-tz-contracts",
            "license": {"name": "MIT"},
        })
        print("Deploying quorum contract")
        initial_storage = self.quorum_contract.storage.encode({
            "admin": self.utils.client.key.public_key_hash(),
            "threshold": threshold,
            "signers": signers,
            "metadata": metadata
        })
        contract_id = self.utils.originate(self.quorum_contract.code, initial_storage)
        _print_contract(contract_id)
        return contract_id
