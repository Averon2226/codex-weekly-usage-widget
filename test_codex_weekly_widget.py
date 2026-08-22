import unittest
import time
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from codex_weekly_widget import CodexWeeklyReader, STALE_AFTER_SECONDS, WeeklySnapshot


def response_headers(**headers: str) -> str:
    values = ", ".join(f'"{name}": "{value}"' for name, value in headers.items())
    return (
        "Request completed method=POST "
        "url=https://chatgpt.com/backend-api/codex/responses "
        f"status=200 OK headers={{{values}}}"
    )


class CodexWeeklyReaderTests(unittest.TestCase):
    def test_remote_snapshot_is_not_overwritten_by_old_log_fallback(self) -> None:
        reset_at = int(time.time()) + 3600
        remote_snapshot = WeeklySnapshot(
            row_id=0,
            used_percent=78,
            remaining_percent=22,
            window_minutes=10080,
            secondary_reset_at=reset_at,
            limit_name="primary",
            source_ts=int(time.time()),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "logs.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE logs "
                "(id INTEGER PRIMARY KEY, ts INTEGER, feedback_log_body TEXT)"
            )
            conn.execute(
                "INSERT INTO logs VALUES (?, ?, ?)",
                (
                    1,
                    remote_snapshot.source_ts - 3600,
                    response_headers(
                        **{
                            "x-codex-primary-used-percent": "51",
                            "x-codex-primary-window-minutes": "10080",
                            "x-codex-primary-reset-at": str(reset_at),
                        }
                    ),
                ),
            )
            conn.commit()
            conn.close()

            reader = CodexWeeklyReader(db_path)
            with patch.object(
                reader,
                "_fetch_remote_usage",
                side_effect=[remote_snapshot, None],
            ):
                first = reader.refresh()
                second = reader.refresh()

        self.assertEqual(first.remaining_percent, 22)
        self.assertEqual(second.remaining_percent, 22)
        self.assertEqual(second.source_ts, remote_snapshot.source_ts)

    def test_reads_current_usage_endpoint_payload(self) -> None:
        payload = {
            "rate_limit": {
                "primary_window": {
                    "used_percent": 66,
                    "limit_window_seconds": 604800,
                    "reset_after_seconds": 444417,
                    "reset_at": 1787205054,
                },
                "secondary_window": None,
            }
        }

        snapshot = CodexWeeklyReader._parse_usage_payload(1786760473, payload)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.remaining_percent, 34)
        self.assertEqual(snapshot.limit_name, "primary")
        self.assertEqual(snapshot.source_ts, 1786760473)

    def test_reads_current_app_server_rate_limits_payload(self) -> None:
        payload = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 21,
                    "windowDurationMins": 10080,
                    "resetsAt": 1787810232,
                },
                "secondary": None,
            },
            "rateLimitsByLimitId": {},
        }

        snapshot = CodexWeeklyReader._parse_app_server_payload(1787300000, payload)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.remaining_percent, 79)
        self.assertEqual(snapshot.limit_name, "primary")
        self.assertEqual(snapshot.secondary_reset_at, 1787810232)

    def test_reads_app_server_limit_id_fallback(self) -> None:
        payload = {
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {
                        "usedPercent": 34,
                        "windowDurationMins": 10080,
                        "resetsAt": 1787810232,
                    }
                }
            }
        }

        snapshot = CodexWeeklyReader._parse_app_server_payload(1787300000, payload)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.remaining_percent, 66)

    def test_prefers_app_server_for_remote_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reader = CodexWeeklyReader(Path(tmp_dir) / "missing.sqlite")
            with patch.object(
                reader._app_server,
                "read_rate_limits",
                return_value={
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 12,
                            "windowDurationMins": 10080,
                            "resetsAt": int(time.time()) + 3600,
                        }
                    }
                },
            ):
                snapshot = reader._fetch_remote_usage(time.time())
            reader.close()

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.remaining_percent, 88)

    def test_reads_current_primary_weekly_bucket(self) -> None:
        body = response_headers(
            **{
                "x-codex-primary-used-percent": "16",
                "x-codex-primary-window-minutes": "10080",
                "x-codex-primary-reset-at": "1784812000",
                "x-codex-secondary-used-percent": "0",
                "x-codex-secondary-window-minutes": "0",
            }
        )

        snapshot = CodexWeeklyReader._parse_snapshot(1, body)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.remaining_percent, 84)
        self.assertEqual(snapshot.limit_name, "primary")

    def test_keeps_compatibility_with_old_secondary_weekly_bucket(self) -> None:
        body = response_headers(
            **{
                "x-codex-primary-used-percent": "20",
                "x-codex-primary-window-minutes": "300",
                "x-codex-secondary-used-percent": "3",
                "x-codex-secondary-window-minutes": "10080",
                "x-codex-secondary-reset-at": "1784355828",
            }
        )

        snapshot = CodexWeeklyReader._parse_snapshot(2, body)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.remaining_percent, 97)
        self.assertEqual(snapshot.limit_name, "secondary")

    def test_ignores_non_weekly_buckets(self) -> None:
        body = response_headers(
            **{
                "x-codex-primary-used-percent": "20",
                "x-codex-primary-window-minutes": "300",
                "x-codex-primary-reset-at": "1784355828",
            }
        )

        self.assertIsNone(CodexWeeklyReader._parse_snapshot(3, body))

    def test_marks_snapshot_stale_after_no_fresh_response(self) -> None:
        snapshot = WeeklySnapshot(
            row_id=4,
            used_percent=16,
            remaining_percent=84,
            window_minutes=10080,
            secondary_reset_at=1784812000,
            limit_name="primary",
            source_ts=int(time.time()) - STALE_AFTER_SECONDS - 1,
        )

        self.assertTrue(CodexWeeklyReader.is_stale(snapshot))


if __name__ == "__main__":
    unittest.main()
