#!/usr/bin/python3

import ast
import atexit
from hashlib import sha1
import importlib
import json
from pathlib import Path

from brownie.project import check_for_project
from brownie.project import build
from brownie import history
from brownie.network.history import _ContractHistory
from brownie._config import ARGV


def get_ast_hash(path):
    '''Generates a hash based on the AST of a script.

    Args:
        path: path of the script to hash

    Returns: sha1 hash as bytes'''
    with Path(path).open() as fp:
        ast_list = [ast.parse(fp.read(), path)]
    base_path = str(check_for_project(path))
    for obj in [i for i in ast_list[0].body if type(i) in (ast.Import, ast.ImportFrom)]:
        if type(obj) is ast.Import:
            name = obj.names[0].name
        else:
            name = obj.module
        origin = importlib.util.find_spec(name).origin
        if base_path in origin:
            with open(origin) as fp:
                ast_list.append(ast.parse(fp.read(), origin))
    dump = "\n".join(ast.dump(i) for i in ast_list)
    return sha1(dump.encode()).hexdigest()


class UpdateManager:

    def __init__(self, path):
        self.path = path
        self.conf_hashes = dict(
            (str(i.absolute()), get_ast_hash(i)) for i in Path(path).glob('tests/**/conftest.py')
        )
        try:
            with open(path) as fp:
                hashes = json.load(fp)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            hashes = {'tests': {}, 'contracts': {}, 'tx': {}}

        self.tests = dict(
            (k, v) for k, v in hashes['tests'].items() if
            Path(k).exists() and self._get_hash(k) == v['sha1']
        )
        self.contracts = dict((k, v['bytecodeSha1']) for k, v in build.items() if v['bytecode'])
        changed_contracts = set(
            k for k, v in hashes['contracts'].items() if
            k not in self.contracts or v != self.contracts[k]
        )
        if changed_contracts:
            for txhash, coverage_eval in hashes['tx'].items():
                if not changed_contracts.intersection(coverage_eval.keys()):
                    history.add_coverage(txhash, coverage_eval)
            self.tests = dict(
                (k, v) for k, v in self.tests.items() if v['isolated'] is not False
                and not changed_contracts.intersection(v['isolated'])
            )
        else:
            for txhash, coverage_eval in hashes['tx'].items():
                history.add_coverage(txhash, coverage_eval)
        atexit.register(self._save_json)
        return

    def set_isolated(self, paths):
        self.isolated = paths

    def _get_hash(self, path):
        hash_ = get_ast_hash(path)
        for confpath in filter(lambda k: k in path, sorted(self.conf_hashes)):
            hash_ += confpath
        return sha1(hash_.encode()).hexdigest()

    def check_updated(self, path):
        path = str(path)
        if path not in self.tests or not self.tests[path]['isolated']:
            return False
        if ARGV['coverage'] and not self.tests[path]['coverage']:
            return False
        return True

    def finish_module(self, path):
        path = str(path)
        isolated = False
        if path in self.isolated:
            isolated = [i for i in _ContractHistory().dependencies() if i in self.contracts]
        self.tests[path] = {
            'sha1': self._get_hash(path),
            'isolated': isolated,
            'coverage': ARGV['coverage'] or (path in self.tests and self.tests[path]['coverage']),
            'txhash': history.get_coverage_hashes()
        }

    def _save_json(self):
        report = {
            'tests': self.tests,
            'contracts': self.contracts,
            'tx': history.get_coverage()
        }
        with open(self.path, 'w') as fp:
            json.dump(report, fp, indent=2, sort_keys=True, default=sorted)
