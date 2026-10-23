"""Fóruns e ações enriquecem a home sem inflar os indicadores de 1:1."""
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cortex.views.dashboard import _meeting_dashboard


class MeetingsDashboardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as db:
            for statement in (
                'CREATE TABLE meetings (id INTEGER PRIMARY KEY, occurred_on TEXT)',
                'CREATE TABLE meeting_participants (meeting_id INTEGER, person_id INTEGER)',
                'CREATE TABLE meeting_actions (id INTEGER PRIMARY KEY, person_id INTEGER, status TEXT, due_date TEXT)',
                "INSERT INTO meetings VALUES (1, '2026-10-20'), (2, '2020-01-01'), (3, '2027-01-01')",
                'INSERT INTO meeting_participants VALUES (1,1), (1,2), (2,1), (3,1)',
                "INSERT INTO meeting_actions VALUES (1,1,'open','2026-10-01'), (2,NULL,'open',NULL), (3,2,'done','2026-10-01'), (4,2,'cancelled','2026-10-01')",
            ):
                db.execute(text(statement))
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)
        self.today = date(2026, 10, 23)

    def test_shared_meeting_counted_once_and_person_metrics_remain_distinct(self):
        pulse = [{'person_id': 1, 'sessions_90d': 3, 'has_session_this_month': False,
                  'last_sentiment': 'neutro', 'overdue_commitments': 0}, {'person_id': 2}]
        with patch('cortex.views.dashboard.load_meeting_context', return_value={'meetings': [], 'actions': []}):
            result = _meeting_dashboard(self.db, self.today, pulse)
        self.assertEqual(result['kpis'], {'meetings_30d': 1, 'open_actions': 2, 'overdue_actions': 1})
        self.assertEqual(pulse[0]['group_meetings_90d'], 1)
        self.assertEqual(pulse[1]['group_meetings_90d'], 1)
        self.assertEqual(pulse[0]['open_meeting_actions'], 1)
        self.assertEqual(pulse[0]['overdue_meeting_actions'], 1)
        self.assertEqual(pulse[1]['open_meeting_actions'], 0)
        self.assertEqual(pulse[0]['sessions_90d'], 3)
        self.assertFalse(pulse[0]['has_session_this_month'])
        self.assertEqual(pulse[0]['last_sentiment'], 'neutro')
        self.assertEqual(pulse[0]['overdue_commitments'], 0)

    def test_only_current_recent_evidence_enters_strategy_and_counts_are_not_list_limits(self):
        base = {'title': 'Fórum da área', 'forum': 'Estratégia', 'summary_ai': 'Síntese',
                'extraction': {'strategic_themes': ['Autonomia', 'Autonomia'], 'risks': ['Dependência operacional']}}
        meetings = [{**base, 'id': 1, 'occurred_on': '2026-10-20', 'analysis_stale': False},
                    {**base, 'id': 2, 'occurred_on': '2026-10-21', 'analysis_stale': True},
                    {**base, 'id': 3, 'occurred_on': '2020-01-01', 'analysis_stale': False},
                    {**base, 'id': 4, 'occurred_on': '2027-01-01', 'analysis_stale': False}]
        for action_id in range(5, 20):
            self.db.execute(text("INSERT INTO meeting_actions VALUES (:id, NULL, 'open', NULL)"), {'id': action_id})
        actions = [{'id': n, 'meeting_id': 1, 'description': f'Ação {n}'} for n in range(20)]
        with patch('cortex.views.dashboard.load_meeting_context', return_value={'meetings': meetings, 'actions': actions}):
            result = _meeting_dashboard(self.db, self.today, [])
        self.assertEqual(result['kpis']['open_actions'], 17)
        self.assertEqual(len(result['actions']), 12)
        self.assertEqual(len(result['strategic_themes']), 1)
        self.assertEqual(result['strategic_themes'][0]['meeting_id'], 1)
        self.assertEqual(result['risks'][0]['text'], 'Dependência operacional')
        self.assertEqual({m['id'] for m in result['recent_meetings']}, {1, 2, 3})


if __name__ == '__main__':
    unittest.main()
