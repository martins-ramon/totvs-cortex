"""Database lifecycle checks use mocks and never contact PostgreSQL."""
import io
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

from cortex import database
from cortex.init_db import main


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {
            "DATABASE_URL": "postgresql://user:password@localhost:6543/cortex",
            "VERCEL": "1",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("_engine", "_SessionLocal", "INIT_DB_STATUS"):
            state = patch.object(database, name, None)
            state.start()
            self.addCleanup(state.stop)

    @patch.object(database, "create_engine")
    def test_vercel_engine_is_lazy_and_does_not_keep_idle_connections(self, create):
        first = database.get_engine()
        self.assertIs(database.get_engine(), first)
        create.assert_called_once()
        self.assertIs(create.call_args.kwargs["poolclass"], NullPool)
        self.assertEqual(create.call_args.kwargs["connect_args"], {"connect_timeout": 10})
        first.connect.assert_not_called()

    @patch.dict("os.environ", {"VERCEL": "0"})
    @patch.object(database, "create_engine")
    def test_local_engine_retains_existing_pool_behavior(self, create):
        database.get_engine()
        self.assertNotIn("poolclass", create.call_args.kwargs)
        self.assertEqual(create.call_args.kwargs["pool_recycle"], 3600)

    @patch.dict("os.environ", {}, clear=True)
    @patch.object(database, "create_engine")
    def test_missing_database_is_resilient(self, create):
        self.assertFalse(database.init_db())
        self.assertEqual(database.INIT_DB_STATUS, "unconfigured")
        self.assertEqual(database.check_database(), "unconfigured")
        create.assert_not_called()

    @patch.object(database, "get_engine")
    def test_connection_failure_never_escapes_startup(self, engine):
        engine.return_value.connect.side_effect = RuntimeError("sensitive connection detail")
        self.assertFalse(database.init_db())
        self.assertEqual(database.INIT_DB_STATUS, "error")
        self.assertEqual(database.check_database(), "error")

    @patch.object(database, "get_engine")
    def test_schema_init_applies_all_steps(self, engine):
        conn = engine.return_value.connect.return_value.__enter__.return_value
        statements = database._AUTH_DDL + database._NEW_DDL + database._EVOLUTION_DDL
        self.assertTrue(database.init_db())
        self.assertEqual(database.INIT_DB_STATUS, "ok")
        self.assertEqual(conn.execute.call_count, len(statements))
        self.assertEqual(conn.commit.call_count, len(statements))
        conn.rollback.assert_not_called()

    @patch.object(database, "get_engine")
    def test_evolution_failure_is_reported_instead_of_false_success(self, engine):
        conn = engine.return_value.connect.return_value.__enter__.return_value
        initial_steps = len(database._AUTH_DDL + database._NEW_DDL)
        conn.execute.side_effect = [None] * initial_steps + [RuntimeError("DDL failed")]
        self.assertFalse(database.init_db())
        self.assertEqual(database.INIT_DB_STATUS, "error")
        conn.rollback.assert_called_once()

    @patch.object(database, "get_engine")
    def test_health_check_is_read_only_and_does_not_set_init_status(self, engine):
        conn = engine.return_value.connect.return_value.__enter__.return_value
        self.assertEqual(database.check_database(), "ok")
        statements = [str(call.args[0]).strip() for call in conn.execute.call_args_list]
        self.assertEqual(statements[0], "SELECT 1")
        self.assertIn("WHERE FALSE", statements[1])
        self.assertIn("one_on_ones.extraction_json", statements[1])
        self.assertIn("card_jobs.progress_json", statements[1])
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))
        self.assertIsNone(database.INIT_DB_STATUS)
        conn.commit.assert_not_called()

    @patch.object(database, "get_engine")
    def test_missing_table_and_column_have_distinct_readiness_status(self, engine):
        conn = engine.return_value.connect.return_value.__enter__.return_value
        for sqlstate in ("42P01", "42703"):
            with self.subTest(sqlstate=sqlstate):
                original = MagicMock(pgcode=sqlstate)
                error = ProgrammingError("SELECT", {}, original)
                conn.execute.side_effect = [None, error]
                self.assertEqual(database.check_database(), "schema_missing")


class InitializationCommandTests(unittest.TestCase):
    @patch("sys.stdout", new_callable=io.StringIO)
    @patch.object(database, "init_db", return_value=True)
    def test_success_exit_code(self, init, output):
        self.assertEqual(main(), 0)
        init.assert_called_once_with()
        self.assertIn("sucesso", output.getvalue())

    @patch("sys.stderr", new_callable=io.StringIO)
    @patch.object(database, "init_db", return_value=False)
    def test_failure_exit_code(self, init, output):
        with patch.object(database, "INIT_DB_STATUS", "unconfigured"):
            self.assertEqual(main(), 1)
        self.assertIn("DATABASE_URL", output.getvalue())


if __name__ == "__main__":
    unittest.main()
