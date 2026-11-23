"""监管更正回执登记。

回执是对已受理报送单据（reports.csv 中的 ticket）的监管端事件：
- ACCEPT  监管端受理更正，带来新报送值；
- WITHDRAW 监管端撤回原受理，该单据在对账中不再计入。

规则与分钟修订一致：只采信收件截止（window.cutoff）之前到达的回执；
同一回执号重试不重复登记；回执号复用但内容不一致时整组隔离，不挑其中一条。
"""
from collections import defaultdict

from app.monitor.clock import instant, minute
from app.numbers import EMPTY, display, number

EVENTS = ('ACCEPT', 'WITHDRAW')


def index_receipts(rows, window):
    """校验回执原件并按回执号归组，返回 (回执组, 隔离行)。"""
    grouped = defaultdict(list)
    isolated = []
    for row in rows:
        fields = row['fields']
        try:
            hour = minute(fields['hour'])
            received = instant(fields['received'])
            if fields['stack'] != window.stack:
                raise ValueError('WRONG_STACK')
            if hour.minute:
                raise ValueError('BAD_TIME')
            if not window.start <= hour < window.end:
                raise ValueError('OUTSIDE_WINDOW')
            if received > window.cutoff:
                raise ValueError('AFTER_CUTOFF')
            event = fields['event'].strip().upper()
            if event not in EVENTS:
                raise ValueError('BAD_EVENT')
            receipt = fields['receipt'].strip()
            ticket = fields['ticket'].strip()
            if not receipt or not ticket or len(receipt) > 40 or len(ticket) > 40:
                raise ValueError('BAD_RECEIPT')
            if event == 'WITHDRAW' and fields['value'].strip() not in EMPTY:
                raise ValueError('WITHDRAW_HAS_VALUE')
            amount = number(fields['value']) if event == 'ACCEPT' else None
            if amount is not None and amount < 0:
                raise ValueError('NEGATIVE')
        except ValueError as error:
            isolated.append(row | {'code': str(error)})
            continue
        grouped[receipt].append({'source': 'receipts', 'line': row['line'],
                                 'raw': row['raw'], 'fields': fields, 'hour': hour,
                                 'received': received, 'event': event, 'ticket': ticket,
                                 'amount': amount})
    return grouped, isolated


def build_chains(rows, report_rows, window):
    """把回执事件串到报送单据上，返回 (单据状态表, 隔离行, 登记统计)。

    单据以 (单据号, 小时) 为键，记录当前是否有效、采用值与依据回执。
    原始报送单视为已受理基线；只有回执链上的最后一个有效事件能改变它。
    同一小时出现同号不同值的报送单属于报送冲突，不建链、交由对账裁决。
    """
    grouped, isolated = index_receipts(rows, window)
    chains = {}
    for row in report_rows:
        key = (row['fields']['ticket'], minute(row['fields']['hour']))
        if key in chains:
            # 同号不同值的报送冲突保留两条原件，链挂首条，最终由对账金额集合裁决。
            continue
        chains[key] = {'ticket': row['fields']['ticket'], 'hour': key[1], 'active': True,
                       'amount': row['amount'], 'basis': row, 'receipts': []}
    registered = 0
    for receipt, events in sorted(grouped.items()):
        first = events[0]
        signature = {(item['hour'], item['event'], item['ticket'],
                      None if item['amount'] is None else format(item['amount'], 'f'))
                     for item in events}
        if len(signature) > 1:
            isolated.extend({'source': 'receipts', 'line': item['line'],
                             'raw': list(item['fields'].values()), 'fields': item['fields'],
                             'code': 'RECEIPT_CONFLICT'} for item in events)
            continue
        # 同一次回执重试：除首条外全部按重复件隔离，不重复登记。
        isolated.extend({'source': 'receipts', 'line': item['line'],
                         'raw': list(item['fields'].values()), 'fields': item['fields'],
                         'code': 'DUPLICATE'} for item in events[1:])
        key = (first['ticket'], first['hour'])
        chain = chains.get(key)
        if chain is None:
            same_ticket = [item for item in chains.values() if item['ticket'] == first['ticket']]
            code = 'RECEIPT_HOUR_MISMATCH' if same_ticket else 'UNKNOWN_TICKET'
            isolated.append({'source': 'receipts', 'line': first['line'],
                             'raw': list(first['fields'].values()), 'fields': first['fields'],
                             'code': code})
            continue
        chain['receipts'].append(first)
        registered += 1
        if first['event'] == 'WITHDRAW':
            chain['active'] = False
        else:
            chain['active'] = True
            chain['amount'] = first['amount']
            chain['basis'] = first
    stats = {'input': len(rows), 'registered': registered,
             'duplicate': sum(1 for row in isolated if row['code'] == 'DUPLICATE'),
             'isolated': len(isolated)}
    return chains, sorted(isolated, key=lambda row: row['line']), stats


def describe(chain):
    """对账页展示用：单据采用值与回执依据。"""
    applied = chain['receipts'][-1] if chain['receipts'] else None
    return {'ticket': chain['ticket'], 'active': chain['active'],
            'amount': None if chain['amount'] is None else display(chain['amount']),
            'basis_source': chain['basis'].get('source', 'reports'),
            'basis_line': chain['basis']['line'],
            'receipt': None if applied is None else applied['fields']['receipt'],
            'receipt_event': None if applied is None else applied['event'],
            'receipt_line': None if applied is None else applied['line'],
            'received': None if applied is None else applied['received'].isoformat()}
