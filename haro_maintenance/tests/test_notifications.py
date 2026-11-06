import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scheduled import batch_refresh, refresh_rows


def report(status='queued', **extra):
    return {'jobs': [dict(id='a', app='demo', source='test', target='preview', status=status, **extra)]}


class NotificationTests(unittest.TestCase):
    def test_pending_survives_and_only_changed_rows_flush(self):
        original=report('queued'); original['jobs'].append(dict(id='old',app='old',status='merged'))
        state={'seen':refresh_rows(original),'last_summary_at':0}
        new=report('merged');new['jobs'].append(original['jobs'][1])
        output,state=batch_refresh(new,state,600,18000)
        self.assertIsNone(output)
        output,state=batch_refresh(new,state,18000,18000)
        self.assertEqual([j['id'] for j in output['jobs']],['a'])
        output,state=batch_refresh(new,state,36000,18000)
        self.assertIsNone(output)

    def test_urgent_immediate_dedup_and_recurrence(self):
        bad=report('blocked',error='owner denied')
        output,state=batch_refresh(bad,{},10,18000)
        self.assertTrue(output['attention'])
        output,state=batch_refresh(bad,state,610,18000)
        self.assertIsNone(output)
        output,state=batch_refresh(report('queued'),state,1210,18000)
        self.assertIsNone(output)
        output,state=batch_refresh(bad,state,1810,18000)
        self.assertTrue(output['attention'])

    def test_urgent_does_not_flush_or_delay_normal_pending(self):
        output,state=batch_refresh(report(),{},0,18000)
        bad={'jobs':[dict(id='b',app='other',status='blocked',error='error')]}
        output,state=batch_refresh(bad,state,600,18000)
        self.assertEqual(output['jobs'][0]['id'],'b')
        output,state=batch_refresh(bad,state,18000,18000)
        self.assertEqual([j['id'] for j in output['jobs']],['a'])

    def test_errors_dedup_and_partial_report_keeps_pending(self):
        _,state=batch_refresh(report(),{},0,18000)
        output,state=batch_refresh({'error':'HTTP 502'},state,600,18000)
        self.assertEqual(output['errors'],['HTTP 502'])
        output,state=batch_refresh({'error':'HTTP 502'},state,1200,18000)
        self.assertIsNone(output)
        output,state=batch_refresh(report(),state,18000,18000)
        self.assertEqual(len(output['jobs']),1)

    def test_consistency_changes_alert_even_with_same_status(self):
        original=report('merged',consistency=[{'target':'preview','status':'consistent'}])
        state={'seen':refresh_rows(original),'last_summary_at':0}
        output,state=batch_refresh(report('merged',consistency=[{'target':'preview','status':'needs_engineer'}]),state,600,18000)
        self.assertTrue(output['attention'])

    def test_all_changed_rows_retained_beyond_old_thirty_limit(self):
        r={'jobs':[dict(id=str(i),app=str(i),status='merged') for i in range(120)]}
        _,state=batch_refresh(r,{},0,18000)
        output,state=batch_refresh(r,state,18000,18000)
        self.assertEqual(len(output['jobs']),120)
