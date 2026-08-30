/*
 * Fixed, read-only statistics snapshot for the virtual ZD1200.
 *
 * This is deliberately not a SQL gateway: it takes no arguments, opens the
 * controller's existing database read-only, and executes only the fixed
 * queries below. Its JSON is consumed by a same-origin static admin page.
 */
#include <sqlite3.h>

#include <stdio.h>
#include <sys/stat.h>

#define STATISTICS_DB "/writable/zd1200-analytics/statistic.db"

static int scalar_int(sqlite3 *db, const char *sql, sqlite3_int64 *value)
{
    sqlite3_stmt *statement = NULL;
    int rc = sqlite3_prepare_v2(db, sql, -1, &statement, NULL);
    if (rc == SQLITE_OK && sqlite3_step(statement) == SQLITE_ROW) {
        *value = sqlite3_column_int64(statement, 0);
        rc = SQLITE_OK;
    } else if (rc == SQLITE_OK) {
        rc = SQLITE_ERROR;
    }
    sqlite3_finalize(statement);
    return rc;
}

int main(void)
{
    struct stat metadata;
    sqlite3 *db = NULL;
    sqlite3_int64 table_exists = 0;
    sqlite3_int64 row_count = 0;
    int rc;

    if (stat(STATISTICS_DB, &metadata) != 0) {
        puts("{\"status\":\"waiting\",\"database_present\":false}");
        return 0;
    }
    /* Never create or modify the controller's live statistics database. */
    rc = sqlite3_open_v2(STATISTICS_DB, &db, SQLITE_OPEN_READONLY, NULL);
    if (rc != SQLITE_OK) {
        printf("{\"status\":\"error\",\"database_present\":true,"
               "\"reason\":\"open_failed\",\"sqlite_code\":%d,"
               "\"sqlite_extended_code\":%d,\"sqlite_message\":\"%s\"}\n",
               rc, db ? sqlite3_extended_errcode(db) : rc,
               db ? sqlite3_errmsg(db) : "no database handle");
        sqlite3_close(db);
        return 0;
    }
    rc = scalar_int(db,
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='statis_client_h'", &table_exists);
    if (rc == SQLITE_OK && table_exists != 0) {
        rc = scalar_int(db, "SELECT count(*) FROM statis_client_h", &row_count);
    }
    if (rc != SQLITE_OK) {
        puts("{\"status\":\"error\",\"database_present\":true,\"reason\":\"query_failed\"}");
    } else {
        printf("{\"status\":\"ok\",\"database_present\":true,"
               "\"database_bytes\":%lld,\"hourly_client_table_present\":%s,"
               "\"hourly_client_rows\":%lld}\n",
               (long long) metadata.st_size,
               table_exists ? "true" : "false",
               (long long) row_count);
    }
    sqlite3_close(db);
    return 0;
}
