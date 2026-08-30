/*
 * Fixed, read-only traffic snapshot for the virtual ZD1200.
 *
 * The program takes no input. It opens a stable copy of ZoneDirector's
 * existing hourly accounting database and exposes only the fixed aggregation
 * needed by the same-origin analytics page.
 */
#include <sqlite3.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define STATISTICS_DB "/writable/zd1200-analytics/statistic.db"
#define AP_LIST "/writable/etc/airespider/ap-list.xml"
#define WLAN_SERVICE_LIST "/writable/etc/airespider/wlansvc-list.xml"
#define HOURS 24

struct traffic_group {
    char *key;
    char *name;
    sqlite3_int64 tx_bytes;
    sqlite3_int64 rx_bytes;
    sqlite3_int64 hourly_tx[HOURS];
    sqlite3_int64 hourly_rx[HOURS];
    struct traffic_group *next;
};

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

static void print_json_string(const char *text)
{
    const unsigned char *p;

    putchar('"');
    for (p = (const unsigned char *)(text ? text : ""); *p; ++p) {
        switch (*p) {
        case '"': fputs("\\\"", stdout); break;
        case '\\': fputs("\\\\", stdout); break;
        case '\n': fputs("\\n", stdout); break;
        case '\r': fputs("\\r", stdout); break;
        case '\t': fputs("\\t", stdout); break;
        default:
            if (*p < 0x20)
                printf("\\u%04x", *p);
            else
                putchar(*p);
        }
    }
    putchar('"');
}

static struct traffic_group *find_group(struct traffic_group **groups,
                                        const char *key)
{
    struct traffic_group *group;
    size_t key_length;

    for (group = *groups; group; group = group->next) {
        if (strcmp(group->key, key) == 0)
            return group;
    }
    group = calloc(1, sizeof(*group));
    if (!group)
        return NULL;
    key_length = strlen(key) + 1;
    group->key = malloc(key_length);
    if (!group->key) {
        free(group);
        return NULL;
    }
    memcpy(group->key, key, key_length);
    group->next = *groups;
    *groups = group;
    return group;
}

static char *copy_string(const char *text)
{
    size_t length = strlen(text) + 1;
    char *copy = malloc(length);
    if (copy)
        memcpy(copy, text, length);
    return copy;
}

static void set_group_name(struct traffic_group *group, const char *name)
{
    char *copy;

    if (!group || group->name || !name || !*name)
        return;
    copy = copy_string(name);
    if (copy)
        group->name = copy;
}

static void add_traffic(struct traffic_group **groups, const char *key,
                        const char *name,
                        sqlite3_int64 tx_bytes, sqlite3_int64 rx_bytes,
                        int hour)
{
    struct traffic_group *group = find_group(groups, key);
    if (!group)
        return;
    set_group_name(group, name);
    group->tx_bytes += tx_bytes;
    group->rx_bytes += rx_bytes;
    if (hour >= 0 && hour < HOURS) {
        group->hourly_tx[hour] += tx_bytes;
        group->hourly_rx[hour] += rx_bytes;
    }
}

static char *xml_attribute(const char *tag, const char *tag_end,
                           const char *attribute)
{
    char needle[80];
    const char *value;
    const char *value_end;
    size_t length;

    snprintf(needle, sizeof(needle), " %s=\"", attribute);
    value = strstr(tag, needle);
    if (!value || value >= tag_end)
        return NULL;
    value += strlen(needle);
    value_end = strchr(value, '"');
    if (!value_end || value_end > tag_end)
        return NULL;
    length = (size_t)(value_end - value);
    {
        char *result = malloc(length + 1);
        if (result) {
            memcpy(result, value, length);
            result[length] = '\0';
        }
        return result;
    }
}

static char *xml_name_for_id(const char *path, const char *element,
                             const char *id, const char *name_attribute)
{
    FILE *file;
    long size;
    char *content;
    char tag_prefix[80];
    char *tag;

    file = fopen(path, "r");
    if (!file)
        return NULL;
    if (fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) < 0
        || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    content = malloc((size_t)size + 1);
    if (!content || fread(content, 1, (size_t)size, file) != (size_t)size) {
        free(content);
        fclose(file);
        return NULL;
    }
    fclose(file);
    content[size] = '\0';
    snprintf(tag_prefix, sizeof(tag_prefix), "<%s ", element);
    tag = content;
    while ((tag = strstr(tag, tag_prefix)) != NULL) {
        char *tag_end = strchr(tag, '>');
        char *candidate_id;
        char *result = NULL;
        if (!tag_end)
            break;
        candidate_id = xml_attribute(tag, tag_end, "id");
        if (candidate_id && strcmp(candidate_id, id) == 0)
            result = xml_attribute(tag, tag_end, name_attribute);
        free(candidate_id);
        if (result) {
            free(content);
            return result;
        }
        tag = tag_end + 1;
    }
    free(content);
    return NULL;
}

static void resolve_group_names(struct traffic_group *groups, const char *path,
                                const char *element, const char *attribute)
{
    while (groups) {
        char *name = xml_name_for_id(path, element, groups->key, attribute);
        if (name) {
            set_group_name(groups, name);
            free(name);
        }
        groups = groups->next;
    }
}

static int group_count(const struct traffic_group *groups)
{
    int count = 0;
    while (groups) {
        ++count;
        groups = groups->next;
    }
    return count;
}

static int compare_groups(const void *left, const void *right)
{
    const struct traffic_group *a = *(const struct traffic_group * const *)left;
    const struct traffic_group *b = *(const struct traffic_group * const *)right;
    sqlite3_int64 total_a = a->tx_bytes + a->rx_bytes;
    sqlite3_int64 total_b = b->tx_bytes + b->rx_bytes;
    return total_a < total_b ? 1 : total_a > total_b ? -1 : strcmp(a->key, b->key);
}

static void print_groups(const char *name, struct traffic_group *groups)
{
    int count = group_count(groups);
    int i;
    struct traffic_group **sorted;

    printf("\"%s\":[", name);
    if (count == 0) {
        putchar(']');
        return;
    }
    sorted = calloc((size_t)count, sizeof(*sorted));
    if (!sorted) {
        putchar(']');
        return;
    }
    for (i = 0; groups; groups = groups->next)
        sorted[i++] = groups;
    qsort(sorted, (size_t)count, sizeof(*sorted), compare_groups);
    for (i = 0; i < count; ++i) {
        int hour;
        struct traffic_group *group = sorted[i];
        if (i)
            putchar(',');
        fputs("{\"id\":", stdout);
        print_json_string(group->key);
        fputs(",\"name\":", stdout);
        print_json_string(group->name ? group->name : "");
        printf(",\"tx_bytes\":%lld,\"rx_bytes\":%lld,\"hours\":[",
               (long long)group->tx_bytes, (long long)group->rx_bytes);
        for (hour = 0; hour < HOURS; ++hour) {
            if (hour)
                putchar(',');
            printf("[%lld,%lld]", (long long)group->hourly_tx[hour],
                   (long long)group->hourly_rx[hour]);
        }
        fputs("]}", stdout);
    }
    free(sorted);
    putchar(']');
}

static void free_groups(struct traffic_group *groups)
{
    while (groups) {
        struct traffic_group *next = groups->next;
        free(groups->key);
        free(groups->name);
        free(groups);
        groups = next;
    }
}

int main(void)
{
    struct stat metadata;
    sqlite3 *db = NULL;
    sqlite3_stmt *statement = NULL;
    sqlite3_int64 max_time = 0;
    sqlite3_int64 window_start = 0;
    sqlite3_int64 table_exists = 0;
    struct traffic_group *clients = NULL;
    struct traffic_group *aps = NULL;
    struct traffic_group *ssids = NULL;
    int rc;

    if (stat(STATISTICS_DB, &metadata) != 0) {
        puts("{\"status\":\"waiting\",\"database_present\":false}");
        return 0;
    }
    rc = sqlite3_open_v2(STATISTICS_DB, &db, SQLITE_OPEN_READONLY, NULL);
    if (rc != SQLITE_OK) {
        printf("{\"status\":\"error\",\"database_present\":true,"
               "\"reason\":\"open_failed\",\"sqlite_code\":%d}\n", rc);
        sqlite3_close(db);
        return 0;
    }
    rc = scalar_int(db,
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='statis_client_h'", &table_exists);
    if (rc == SQLITE_OK && table_exists)
        rc = scalar_int(db, "SELECT coalesce(max(time), 0) FROM statis_client_h",
                        &max_time);
    if (rc != SQLITE_OK) {
        puts("{\"status\":\"error\",\"database_present\":true,\"reason\":\"query_failed\"}");
        sqlite3_close(db);
        return 0;
    }
    if (max_time > (HOURS - 1) * 3600)
        window_start = max_time - (HOURS - 1) * 3600;
    if (table_exists && max_time) {
        rc = sqlite3_prepare_v2(db,
            "SELECT time, client_mac, ap_id, ssid_id, tx_bytes, rx_bytes, model "
            "FROM statis_client_h WHERE time >= ? AND time <= ?", -1,
            &statement, NULL);
        if (rc == SQLITE_OK) {
            sqlite3_bind_int64(statement, 1, window_start);
            sqlite3_bind_int64(statement, 2, max_time);
            while ((rc = sqlite3_step(statement)) == SQLITE_ROW) {
                char ap_key[32];
                char ssid_key[32];
                sqlite3_int64 row_time = sqlite3_column_int64(statement, 0);
                const char *client = (const char *)sqlite3_column_text(statement, 1);
                const char *model = (const char *)sqlite3_column_text(statement, 6);
                sqlite3_int64 tx_bytes = sqlite3_column_int64(statement, 4);
                sqlite3_int64 rx_bytes = sqlite3_column_int64(statement, 5);
                int hour = (int)((row_time - window_start) / 3600);
                snprintf(ap_key, sizeof(ap_key), "%lld",
                         (long long)sqlite3_column_int64(statement, 2));
                snprintf(ssid_key, sizeof(ssid_key), "%lld",
                         (long long)sqlite3_column_int64(statement, 3));
                add_traffic(&clients, client ? client : "unknown", model,
                            tx_bytes, rx_bytes, hour);
                add_traffic(&aps, ap_key, NULL, tx_bytes, rx_bytes, hour);
                add_traffic(&ssids, ssid_key, NULL, tx_bytes, rx_bytes, hour);
            }
            if (rc == SQLITE_DONE)
                rc = SQLITE_OK;
        }
        sqlite3_finalize(statement);
    }
    if (rc != SQLITE_OK) {
        puts("{\"status\":\"error\",\"database_present\":true,\"reason\":\"traffic_query_failed\"}");
    } else {
        resolve_group_names(aps, AP_LIST, "ap", "name");
        resolve_group_names(ssids, WLAN_SERVICE_LIST, "wlansvc", "ssid");
        printf("{\"status\":\"ok\",\"database_present\":true,"
               "\"database_bytes\":%lld,\"window_end\":%lld,"
               "\"window_start\":%lld,\"hourly_client_table_present\":%s,",
               (long long)metadata.st_size, (long long)max_time,
               (long long)window_start, table_exists ? "true" : "false");
        print_groups("clients", clients);
        putchar(',');
        print_groups("aps", aps);
        putchar(',');
        print_groups("ssids", ssids);
        puts("}");
    }
    free_groups(clients);
    free_groups(aps);
    free_groups(ssids);
    sqlite3_close(db);
    return 0;
}
