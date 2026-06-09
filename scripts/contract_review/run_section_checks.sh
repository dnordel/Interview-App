#!/usr/bin/env bash
set -euo pipefail

python tools/check_contract_review.py --section baseline
python tools/check_contract_review.py --section locked
python tools/check_contract_review.py --section schema
python tools/check_contract_review.py --section coverage-matrix
