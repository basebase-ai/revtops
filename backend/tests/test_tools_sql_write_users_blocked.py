from agents import tools


def test_validate_sql_write_blocks_users_table_updates():
    is_valid, error, operation = tools._validate_sql_write("UPDATE users SET phone_number = '+14155551234' WHERE id = 'x'")
    assert is_valid
    assert operation == "UPDATE"

    table = tools._extract_table_from_write("UPDATE users SET phone_number = '+14155551234' WHERE id = 'x'")
    assert table == "users"
    assert table not in tools.WRITABLE_TABLES
