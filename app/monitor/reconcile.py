from collections import defaultdict
from decimal import Decimal

from app.monitor.clock import minute
from app.monitor.receipts import build_chains, describe
from app.monitor.reports import index_reports
from app.numbers import display


def compare(hours, records, window, receipt_records=None):
    indexed, isolated = index_reports(records, window)
    valid_rows = [row for group in indexed.values() for row in group]
    chains, receipt_isolated, receipt_stats = build_chains(receipt_records or [], valid_rows, window)
    conflict_keys = set()
    seen = set()
    for row in valid_rows:
        key = (row['fields']['ticket'], minute(row['fields']['hour']))
        if key in seen:
            conflict_keys.add(key)
        seen.add(key)
    reports = defaultdict(list)
    for hour, group in indexed.items():
        for row in group:
            key = (row['fields']['ticket'], minute(row['fields']['hour']))
            chain = chains.get(key)
            if chain is not None and not chain['active']:
                continue  # 监管端已撤回受理，该单据不参与对账
            if key in conflict_keys:
                reports[hour].append(row)  # 报送冲突：保留各自原值，不套回执链
            else:
                amount = row['amount'] if chain is None else chain['amount']
                reports[hour].append(row | {'amount': amount, 'ticket': row['fields']['ticket']})
    result = []
    for hour in hours:
        sources = reports.pop(hour['hour'], [])
        amounts = {row['amount'] for row in sources}
        reported = next(iter(amounts)) if len(amounts) == 1 else None
        difference = None
        if len(amounts) > 1:
            code = 'REPORT_CONFLICT'
        elif reported is None:
            code = 'NO_REPORT'
        elif hour['mg_nm3'] is None:
            code = 'NO_REFERENCE'
        else:
            difference = Decimal(hour['mg_nm3']) - Decimal(display(reported))
            code = 'MATCH' if abs(difference) <= window.tolerance else 'DEVIATION'
        adopted = []
        if len(amounts) <= 1:
            for row in sources:
                chain = chains.get((row['fields']['ticket'], minute(row['fields']['hour'])))
                if chain is not None:
                    adopted.append(describe(chain))
        result.append(hour | {'code': code, 'reported_mg_nm3': None if reported is None else display(reported),
                              'difference_mg_nm3': None if difference is None else display(difference),
                              'reports': sources, 'adopted': adopted})
    for excess in reports.values():
        isolated.extend(row | {'code': 'OUTSIDE_REPORT'} for row in excess)
    return result, isolated, receipt_isolated, receipt_stats
