import argparse
from datetime import datetime, timedelta, timezone

from app import config, queue_db


def _now_iso(override: str | None) -> str:
    if override:
        try:
            return datetime.fromisoformat(override.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _delete(conn, sql: str, params: tuple, dry_run: bool) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) AS c FROM ({sql})", params)
    count = cur.fetchone()["c"]
    if dry_run or count == 0:
        return count
    conn.execute(f"DELETE FROM evidence_runs WHERE rowid IN (SELECT rowid FROM ({sql}))", params)
    conn.commit()
    return count


def run_cleanup(args: argparse.Namespace) -> None:
    now_iso = _now_iso(args.now)
    now_dt = _parse_iso(now_iso)
    conn = queue_db.get_connection()
    try:
        # Evidence runs
        ev_sql = """
            SELECT er.rowid FROM evidence_runs er
            JOIN intakes i ON i.intake_id = er.intake_id
            WHERE er.expires_at IS NOT NULL AND er.expires_at < ?
            AND (i.status IN ('resolved','dead_letter') OR ? = 1)
        """
        ev_deleted = _delete(conn, ev_sql, (now_iso, 1 if args.force else 0), args.dry_run)

        # Handoff packs
        ho_sql = """
            SELECT rowid FROM handoff_packs hp
            JOIN intakes i ON i.intake_id = hp.intake_id
            WHERE hp.expires_at IS NOT NULL AND hp.expires_at < ?
            AND (i.status IN ('resolved','dead_letter') OR ? = 1)
        """
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS c FROM ({ho_sql})", (now_iso, 1 if args.force else 0))
        ho_deleted = cur.fetchone()["c"]
        if not args.dry_run and ho_deleted:
            conn.execute(f"DELETE FROM handoff_packs WHERE rowid IN (SELECT rowid FROM ({ho_sql}))", (now_iso, 1 if args.force else 0))
            conn.commit()

        # Intakes soft delete
        grace_dt = now_dt - timedelta(days=config.INTAKE_GRACE_DAYS)
        cur.execute(
            """
            SELECT intake_id FROM intakes
            WHERE resolved_at IS NOT NULL AND resolved_at < ?
            AND (deleted_at IS NULL)
            """,
            (grace_dt.isoformat().replace("+00:00", "Z"),),
        )
        intake_ids = [row["intake_id"] for row in cur.fetchall()]
        int_soft = len(intake_ids)
        if not args.dry_run and intake_ids:
            conn.executemany(
                "UPDATE intakes SET deleted_at = ? WHERE intake_id = ?",
                [(now_iso, iid) for iid in intake_ids],
            )
            conn.commit()

        print(f"[cleanup] dry_run={args.dry_run} force={args.force} now={now_iso}")
        print(f"evidence_runs {'would delete' if args.dry_run else 'deleted'}: {ev_deleted}")
        print(f"handoff_packs {'would delete' if args.dry_run else 'deleted'}: {ho_deleted}")
        print(f"intakes {'would soft-delete' if args.dry_run else 'soft-deleted'}: {int_soft}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Retention cleanup")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true", help="Apply changes (override dry-run)")
    parser.add_argument("--force", action="store_true", help="Allow cleanup of non-resolved statuses")
    parser.add_argument("--now", help="Override current time (ISO)")
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after cleanup (blocking)")
    parser.add_argument("--vacuum-into", help="Run VACUUM INTO <path> after cleanup")
    args = parser.parse_args()
    if args.apply:
        args.dry_run = False
    run_cleanup(args)
    if not args.dry_run:
        if args.vacuum_into:
            conn = queue_db.get_connection()
            try:
                conn.execute(f"VACUUM INTO '{args.vacuum_into}'")
                conn.commit()
            finally:
                conn.close()
        elif args.vacuum:
            conn = queue_db.get_connection()
            try:
                conn.execute("VACUUM")
                conn.commit()
            finally:
                conn.close()


if __name__ == "__main__":
    main()
