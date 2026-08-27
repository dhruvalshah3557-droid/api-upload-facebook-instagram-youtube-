#!/usr/bin/env python3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import open_sheets_with_retry, publish_job


def run_case(sheets, sku, account_id):
    accounts = {a['account_id']: a for a in sheets.get_accounts()}
    sources = sheets.get_source_rows()
    account = accounts.get(account_id)
    source = sources.get(str(sku))
    if not account:
        raise RuntimeError(f'Account not found: {account_id}')
    if not account.get('enabled'):
        raise RuntimeError(f'Account disabled: {account_id}')
    if not source:
        raise RuntimeError(f'SKU not found: {sku}')
    job = {
        'job_id': f'TARGETED-{sku}-{account_id}',
        'sku': str(sku),
        'account_id': account_id,
        'media_selection': 'carousel',
        'platform': account['platform'],
        'format': 'carousel',
        'stock_id_tag': str(sku),
        'language': account.get('primary_language', ''),
    }
    post_id, url = publish_job(job, source, account)
    print(f'PASS {account_id} SKU {sku}: {post_id} {url}')


def main():
    sheets = open_sheets_with_retry()
    cases = [
        ('298', 'FB-BKK'),
        ('8509', 'IG-RUS'),
    ]
    failures = []
    for sku, account_id in cases:
        try:
            run_case(sheets, sku, account_id)
        except Exception as exc:
            failures.append(f'{account_id}/{sku}: {exc}')
            print(f'FAIL {account_id} SKU {sku}: {exc}', file=sys.stderr)
    if failures:
        raise SystemExit('\n'.join(failures))


if __name__ == '__main__':
    main()
