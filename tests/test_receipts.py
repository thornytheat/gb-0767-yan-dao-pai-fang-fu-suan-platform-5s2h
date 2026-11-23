"""监管更正回执与交班对账规则。

场景主线：
- 旧报送单已受理、更正回执晚于收件截止 → 差额仍按旧单，不误报；
- 回执在截止前受理 → 差额采用更正后的新值，页面交代单据与回执；
- 监管端撤回受理 → 该单据失效，小时回到无报送；
- 同一次回执重试 → 只登记一次；
- 晚到回执补齐后重新归档 → 新批次、新差额，旧批次证据仍可核验当时单据。
"""
from hashlib import sha256

from app.archive.certificate import issue
from app.archive.manifest import canonical
from app.archive.run import archive_run
from app.monitor.inspection import Inspection
from app.monitor.receipts import build_chains
from app.monitor.source import sample

HEADER = 'stack,hour,receipt,event,value,ticket,received'
HOUR0 = '2026-11-14T00:00Z'
HOUR1 = '2026-11-14T01:00Z'


def receipt(event, value, ticket, hour=HOUR0, receipt='Z-1', received='2026-11-14T00:20Z',
            stack='K-04'):
    return f'{stack},{hour},{receipt},{event},{value},{ticket},{received}'


def packet(rows):
    files = sample('clean')
    files['receipts'] = HEADER + '\n' + '\n'.join(rows)
    return files


def reconcile(rows):
    audit = Inspection(packet(rows))
    return audit.reports


def test_late_accepted_correction_keeps_old_ticket_value():
    # 交班时刻：更正要到 04:05 才受理，截止是 04:00。差额仍按旧单 30.00 → 复算 30.00。
    reports = reconcile([receipt('ACCEPT', '29.80', 'R-801', received='2026-11-14T04:05Z')])
    row = reports['rows'][0]
    assert (row['code'], row['reported_mg_nm3'], row['difference_mg_nm3']) == ('MATCH', '30.00', '0.00')
    assert reports['receipt_isolated'][0]['code'] == 'AFTER_CUTOFF'
    adopted = row['adopted'][0]
    assert (adopted['ticket'], adopted['receipt'], adopted['amount']) == ('R-801', None, '30.00')


def test_in_cutoff_acceptance_switches_to_corrected_value_with_basis():
    reports = reconcile([receipt('ACCEPT', '29.80', 'R-801')])
    row = reports['rows'][0]
    assert (row['code'], row['reported_mg_nm3'], row['difference_mg_nm3']) == ('MATCH', '29.80', '0.20')
    adopted = row['adopted'][0]
    assert (adopted['receipt'], adopted['receipt_event'], adopted['receipt_line'],
            adopted['amount'], adopted['active']) == ('Z-1', 'ACCEPT', 2, '29.80', True)
    assert adopted['basis_source'] == 'receipts'
    # 对账表保留原报送单据行，新值只体现在采用列，原件可回溯
    assert (row['reports'][0]['source'], row['reports'][0]['fields']['value']) == ('reports', '30.00')


def test_withdrawn_acceptance_leaves_hour_without_report():
    from app.monitor.clock import minute
    from app.monitor.reports import index_reports
    files = packet([receipt('WITHDRAW', '-', 'R-802', hour=HOUR1)])
    audit = Inspection(files)
    reports = audit.reports
    row = reports['rows'][1]
    assert (row['code'], row['reported_mg_nm3'], row['adopted']) == ('NO_REPORT', None, [])
    indexed, _ = index_reports(audit.ledgers['reports'][0], audit.window)
    report_rows = [item for group in indexed.values() for item in group]
    chains, _, stats = build_chains(audit.ledgers['receipts'][0], report_rows, audit.window)
    key = ('R-802', minute(HOUR1))
    assert chains[key]['active'] is False
    assert stats['registered'] == 1


def test_retry_same_receipt_registers_once():
    rows = [receipt('ACCEPT', '29.80', 'R-801', receipt='Z-2', received='2026-11-14T00:20Z'),
            receipt('ACCEPT', '29.80', 'R-801', receipt='Z-2', received='2026-11-14T00:21Z', ),
            receipt('ACCEPT', '29.80', 'R-801', receipt='Z-2', received='2026-11-14T00:22Z')]
    reports = reconcile(rows)
    assert reports['receipt_stats'] == {'input': 3, 'registered': 1, 'duplicate': 2, 'isolated': 2}
    assert [r['code'] for r in reports['receipt_isolated']] == ['DUPLICATE', 'DUPLICATE']
    assert reports['receipt_stats']['isolated'] == len(reports['receipt_isolated'])


def test_dirty_sample_handover_blocks_every_correction_at_cutoff(dirty):
    stats = dirty.reports['receipt_stats']
    assert (stats['input'], stats['registered'], stats['duplicate']) == (6, 0, 0)
    assert stats['isolated'] == len(dirty.reports['receipt_isolated']) == 6
    # 交班差额仍按旧已受理单 R-801，页面依据中没有任何回执
    adopted = dirty.reports['rows'][0]['adopted'][0]
    assert (adopted['ticket'], adopted['receipt'], adopted['amount']) == ('R-801', None, '29.75')


def test_same_receipt_id_with_conflicting_content_is_rejected_whole():
    rows = [receipt('ACCEPT', '29.80', 'R-801', receipt='Z-3', received='2026-11-14T00:20Z'),
            receipt('ACCEPT', '29.90', 'R-801', receipt='Z-3', received='2026-11-14T00:21Z')]
    reports = reconcile(rows)
    assert reports['receipt_stats']['registered'] == 0
    assert {r['code'] for r in reports['receipt_isolated']} == {'RECEIPT_CONFLICT'}
    # 冲突回执不改变采用值
    assert reports['rows'][0]['reported_mg_nm3'] == '30.00'


def test_unknown_ticket_and_hour_mismatch_isolated():
    rows = [receipt('ACCEPT', '1', 'R-NOPE', receipt='Z-4'),
            receipt('WITHDRAW', '-', 'R-802', hour=HOUR0, receipt='Z-5')]
    reports = reconcile(rows)
    codes = {r['code'] for r in reports['receipt_isolated']}
    assert codes == {'RECEIPT_HOUR_MISMATCH', 'UNKNOWN_TICKET'}
    # 小时不符的撤回不得误伤 R-802 在 HOUR1 的有效受理
    assert reports['rows'][1]['code'] == 'MATCH'


def test_withdraw_carrying_value_is_invalid():
    reports = reconcile([receipt('WITHDRAW', '10', 'R-801', receipt='Z-6')])
    assert reports['receipt_isolated'][0]['code'] == 'WITHDRAW_HAS_VALUE'
    assert reports['rows'][0]['adopted'][0]['receipt'] is None


def test_accept_requires_finite_value():
    reports = reconcile([receipt('ACCEPT', 'bad', 'R-801', receipt='Z-7')])
    assert reports['receipt_isolated'][0]['code'] == 'UNSAFE_NUMBER'


def test_late_receipt_then_rereconcile_creates_new_batch_but_old_evidence_holds(client):
    before = sample('clean')
    before['receipts'] = HEADER  # 交班批次：尚无回执
    first, reused = archive_run(client.application.extensions['monitor_archive'],
                                {'files': before})
    assert reused is False
    first_row = first['results']['reports']['rows'][0]
    assert first_row['difference_mg_nm3'] == '0.00'
    first_sha = first['manifest']['run_id']

    # 晚到的更正回执补齐后，同一小时重新对账
    after = dict(before)
    after['receipts'] = HEADER + '\n' + receipt('ACCEPT', '29.80', 'R-801')
    second, reused = archive_run(client.application.extensions['monitor_archive'],
                                 {'files': after})
    assert reused is False
    assert second['manifest']['run_id'] != first_sha
    assert second['results']['reports']['rows'][0]['difference_mg_nm3'] == '0.20'

    # 旧批次仍是不可变证据：原件、回执缺位与当时差额均可核验
    stored = client.application.extensions['monitor_archive'].require(first_sha)
    assert stored == first
    assert stored['results']['reports']['rows'][0]['adopted'][0]['receipt'] is None
    assert issue(stored)['sha256'] == sha256(canonical(
        {'manifest': stored['manifest'], 'originals': stored['originals'],
         'results': stored['results']}).encode()).hexdigest()


def test_rereconcile_identical_packet_reuses_batch(client):
    files = packet([receipt('ACCEPT', '29.80', 'R-801')])
    first, reused_first = archive_run(client.application.extensions['monitor_archive'],
                                      {'files': files})
    second, reused_second = archive_run(client.application.extensions['monitor_archive'],
                                        {'files': files})
    assert (reused_first, reused_second) == (False, True)
    assert first['manifest']['run_id'] == second['manifest']['run_id']


def test_legacy_four_file_packet_still_accepted(client):
    files = {key: value for key, value in sample('clean').items() if key != 'receipts'}
    response = client.post('/api/runs', json={'files': files})
    assert response.status_code == 200
    assert response.json['manifest']['sha256'].get('receipts')


def test_reconciliation_page_explains_ticket_and_receipt_basis(client):
    files = packet([receipt('ACCEPT', '29.80', 'R-801')])
    run_id = client.post('/api/runs', json={'files': files}).json['run_id']
    html = client.get('/monitor/reports?run=' + run_id).get_data(as_text=True)
    for marker in ('差额依据单据与回执', 'Z-1', '更正受理', '回执原件第 2 行', '回执登记（本次对账批次）'):
        assert marker in html
    # 交班批次（无回执）页面明确交代按已受理原值对账
    bare = {key: value for key, value in sample('clean').items() if key != 'receipts'}
    bare_id = client.post('/api/runs', json={'files': bare}).json['run_id']
    bare_html = client.get('/monitor/reports?run=' + bare_id).get_data(as_text=True)
    assert '无在途更正回执，按已受理原值对账' in bare_html
