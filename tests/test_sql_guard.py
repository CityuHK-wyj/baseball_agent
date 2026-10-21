from app.capabilities.analytics.guard import guard_sql


def test_select_allowed():
    assert guard_sql("SELECT COUNT(*) FROM public.statcast_pitches").ok


def test_insert_rejected():
    result = guard_sql("INSERT INTO t VALUES (1)")
    assert not result.ok


def test_multiple_statements_rejected():
    assert not guard_sql("SELECT 1; DROP TABLE t").ok


def test_comment_rejected():
    assert not guard_sql("SELECT 1 -- comment").ok


def test_pg_read_file_rejected():
    assert not guard_sql("SELECT pg_read_file('/etc/passwd')").ok


def test_event_value_truncated_pa_is_not_a_truncate_statement():
    # 'truncated_pa' is a real Statcast event and must not trip the keyword guard.
    sql = "SELECT COUNT(*) FROM public.statcast_pitches WHERE \"events\" IN ('truncated_pa')"
    assert guard_sql(sql).ok
