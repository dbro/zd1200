/* Local ping results and raw-network-snapshot index for virtual ZD1200.
 *
 * The current client view is parsed once to discover clients and build an
 * association lookup. Retained AP/client/mesh snapshots remain opaque XML.
 */
#include <sqlite3.h>
#include <arpa/inet.h>
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <netinet/ip.h>
#include <netinet/ip_icmp.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define DB "/writable/zd1200-ping-monitor/pings.db"
#define AP_LIST "/writable/etc/airespider/ap-list.xml"
#define SNAPSHOT_DIR "/writable/zd1200-ping-monitor/snapshots"
#define RETENTION_SECONDS (30 * 24 * 60 * 60)
#define TIMEOUT_MS 1000
#define PING_BATCH_SIZE 512
#define ICMP_MAGIC 0x5a443132U
#define HOURS 24
#define MAX_BUCKET_SAMPLES 800

struct target { char *id, *name, *ip; struct target *next; };
struct client {
    char *mac, *ip, *name, *ap, *ssid, *radio;
    int snr_db, signal_dbm, noise_floor_dbm;
    int has_snr, has_signal, has_noise;
};
struct client_view { struct client *items; size_t count, capacity; int valid; };
struct measurement {
    sqlite3_int64 id;
    struct in_addr address;
    const char *state;
    long long sent_ms;
    int rtt_ms, responded, pingable;
    int snr_db, signal_dbm, noise_floor_dbm;
    int has_snr, has_signal, has_noise;
};
struct echo_packet {
    struct icmphdr header;
    uint32_t magic;
    uint32_t index;
};
static int add_client(const char *mac, const char *ip, const char *name);

static char *copy_text(const char *value) {
    size_t n; char *copy;
    if (!value) return NULL;
    n=strlen(value); copy=malloc(n+1);
    if (copy) memcpy(copy,value,n+1);
    return copy;
}

static void json(const char *s) {
    const unsigned char *p = (const unsigned char *)(s ? s : "");
    putchar('"');
    for (; *p; ++p) switch (*p) {
    case '"': fputs("\\\"", stdout); break; case '\\': fputs("\\\\", stdout); break;
    case '\n': fputs("\\n", stdout); break; case '\r': fputs("\\r", stdout); break;
    case '\t': fputs("\\t", stdout); break;
    default: if (*p < 0x20) printf("\\u%04x", *p); else putchar(*p);
    }
    putchar('"');
}

static int sql(sqlite3 *db, const char *text) {
    char *error = NULL; int rc = sqlite3_exec(db, text, NULL, NULL, &error);
    sqlite3_free(error); return rc;
}

static int schema(sqlite3 *db) {
    /* This prototype had a structured-context sample table. It contains no
     * production data; remove it rather than retain a second history model. */
    if (sql(db,
        "DROP TABLE IF EXISTS sample;DROP TABLE IF EXISTS hourly_summary;"
        "CREATE TABLE IF NOT EXISTS target("
        "id INTEGER PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN('ap','client')),"
        "ip TEXT NOT NULL,client_mac TEXT UNIQUE,ap_id TEXT UNIQUE,name TEXT NOT NULL,"
        "current_ap TEXT,current_ssid TEXT,current_radio TEXT,"
        "enabled INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL"
        ");"
        "CREATE TABLE IF NOT EXISTS ping_result("
        "id INTEGER PRIMARY KEY,observed_at INTEGER NOT NULL,target_id INTEGER NOT NULL,"
        "rtt_ms INTEGER NOT NULL CHECK(rtt_ms>=0 AND rtt_ms<=1000),"
        "responded INTEGER NOT NULL CHECK(responded IN(0,1)),"
        "state TEXT NOT NULL DEFAULT 'timeout' CHECK(state IN('ok','timeout','not_associated','unknown')),"
        "snr_db INTEGER,signal_dbm INTEGER,noise_floor_dbm INTEGER,"
        "FOREIGN KEY(target_id) REFERENCES target(id));"
        "CREATE INDEX IF NOT EXISTS ping_result_target_time "
        "ON ping_result(target_id,observed_at);"
        "CREATE TABLE IF NOT EXISTS ap_radio_sample("
        "observed_at INTEGER NOT NULL,ap_mac TEXT NOT NULL,radio_id INTEGER NOT NULL,"
        "radio_type TEXT,channel INTEGER,channelization INTEGER,clients INTEGER,"
        "noise_floor_dbm INTEGER,avg_snr_db INTEGER,"
        "airtime_total_tenths INTEGER,airtime_busy_tenths INTEGER,"
        "airtime_rx_tenths INTEGER,airtime_tx_tenths INTEGER,rf_samples INTEGER,"
        "PRIMARY KEY(observed_at,ap_mac,radio_id));"
        "CREATE INDEX IF NOT EXISTS ap_radio_sample_ap_time ON ap_radio_sample(ap_mac,observed_at);"
        "CREATE TABLE IF NOT EXISTS mesh_link_sample("
        "observed_at INTEGER NOT NULL,ap_mac TEXT NOT NULL,peer_mac TEXT NOT NULL,"
        "direction TEXT NOT NULL,radio_type TEXT,snr_db INTEGER,"
        "PRIMARY KEY(observed_at,ap_mac,peer_mac,direction));"
        "CREATE INDEX IF NOT EXISTS mesh_link_sample_ap_time ON mesh_link_sample(ap_mac,observed_at);"
        "CREATE TABLE IF NOT EXISTS zd_event("
        "event_time INTEGER NOT NULL,event_hash TEXT NOT NULL,severity INTEGER,category TEXT,"
        "message_id TEXT,ap_mac TEXT,ap_name TEXT,client_mac TEXT,wlan TEXT,reason TEXT,details TEXT,"
        "PRIMARY KEY(event_time,event_hash));"
        "CREATE INDEX IF NOT EXISTS zd_event_time ON zd_event(event_time);") != SQLITE_OK) return SQLITE_ERROR;

    /* The first prototype used UNIQUE(kind,ip).  That is wrong for mobile
     * clients: a DHCP address can move to a different MAC.  Rebuild only that
     * tiny metadata table, retaining IDs so every old raw sample still refers
     * to exactly the same target. */
    {
        sqlite3_stmt *s = NULL;
        const char *ddl = NULL;
        int legacy = 0;
        if (sqlite3_prepare_v2(db, "SELECT sql FROM sqlite_master WHERE type='table' AND name='target'", -1, &s, NULL) == SQLITE_OK && sqlite3_step(s) == SQLITE_ROW)
            ddl = (const char *)sqlite3_column_text(s, 0);
        if (ddl && (strstr(ddl, "UNIQUE(kind,ip)") || !strstr(ddl, "ap_id TEXT UNIQUE"))) legacy = 1;
        sqlite3_finalize(s);
        if (legacy && sql(db,
            "BEGIN;"
            "CREATE TABLE target_new("
            "id INTEGER PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN('ap','client')),"
            "ip TEXT NOT NULL,client_mac TEXT UNIQUE,ap_id TEXT UNIQUE,name TEXT NOT NULL,"
            "enabled INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL);"
            /* A few development builds could create duplicate AP target
             * metadata. Keep the first ID and repoint all its raw samples
             * before applying the unique AP identity constraint. */
            "UPDATE ping_result SET target_id=(SELECT MIN(t2.id) FROM target t2 "
            "WHERE t2.kind='ap' AND t2.ap_id=(SELECT t3.ap_id FROM target t3 "
            "WHERE t3.id=ping_result.target_id)) WHERE target_id IN "
            "(SELECT id FROM target WHERE kind='ap');"
            "INSERT INTO target_new(id,kind,ip,client_mac,ap_id,name,enabled,created_at) "
            "SELECT id,kind,ip,client_mac,ap_id,name,enabled,created_at FROM target "
            "WHERE kind='client' OR id IN (SELECT MIN(id) FROM target WHERE kind='ap' GROUP BY ap_id);"
            "DROP TABLE target;ALTER TABLE target_new RENAME TO target;COMMIT;") != SQLITE_OK) return SQLITE_ERROR;
    }
    /* Development builds before the raw-sample design did not have a state
     * field. Attempting this on a current database is harmlessly ignored. */
    sql(db, "ALTER TABLE ping_result ADD COLUMN state TEXT NOT NULL DEFAULT 'timeout'");
    sql(db, "ALTER TABLE ping_result ADD COLUMN snr_db INTEGER");
    sql(db, "ALTER TABLE ping_result ADD COLUMN signal_dbm INTEGER");
    sql(db, "ALTER TABLE ping_result ADD COLUMN noise_floor_dbm INTEGER");
    sql(db, "ALTER TABLE target ADD COLUMN current_ap TEXT");
    sql(db, "ALTER TABLE target ADD COLUMN current_ssid TEXT");
    sql(db, "ALTER TABLE target ADD COLUMN current_radio TEXT");
    /* The AP's own airtime-total/busy/rx/tx counters accumulate since the
     * radio driver's last internal reset (roughly every 15 minutes) rather
     * than being instantaneous. rf-samples is the AP's own count of samples
     * folded into that accumulation and resets in lockstep, so retaining it
     * lets the exporter turn the raw counter back into a windowed average
     * instead of a sawtooth. */
    sql(db, "ALTER TABLE ap_radio_sample ADD COLUMN rf_samples INTEGER");
    return SQLITE_OK;
}

static char *attr(const char *tag, const char *end, const char *name) {
    char needle[64]; const char *a, *b; size_t n;
    snprintf(needle, sizeof(needle), " %s=\"", name); a = strstr(tag, needle);
    if (!a || a >= end) return NULL; a += strlen(needle); b = strchr(a, '"');
    if (!b || b > end) return NULL; n = (size_t)(b - a);
    { char *r = malloc(n + 1); if (r) { memcpy(r, a, n); r[n] = 0; } return r; }
}

static struct target *read_aps(void) {
    FILE *f; long size; char *text, *tag; struct target *head = NULL;
    f = fopen(AP_LIST, "r");
    if (!f || fseek(f,0,SEEK_END) || (size=ftell(f))<0 || fseek(f,0,SEEK_SET)) { if(f)fclose(f); return NULL; }
    text = malloc((size_t)size + 1);
    if (!text || fread(text,1,(size_t)size,f)!=(size_t)size) { free(text); fclose(f); return NULL; }
    fclose(f); text[size]=0; tag=text;
    while ((tag=strstr(tag,"<ap ")) != NULL) {
        char *end=strchr(tag,'>'); struct target *t; char *id,*name,*ip;
        if (!end) break; id=attr(tag,end,"mac");name=attr(tag,end,"name");
        if(!name||!*name){free(name);name=attr(tag,end,"ap-name");}
        if(!name||!*name){free(name);name=attr(tag,end,"devname");}
        ip=attr(tag,end,"ip");
        if (id && name && ip) { struct in_addr tmp; if (inet_pton(AF_INET,ip,&tmp)==1 &&
            (t=calloc(1,sizeof(*t))) != NULL) { t->id=id;t->name=name;t->ip=ip;t->next=head;head=t; id=name=ip=NULL; } }
        free(id);free(name);free(ip);tag=end+1;
    }
    free(text); return head;
}

static void free_targets(struct target *t) { while(t){struct target*n=t->next;free(t->id);free(t->name);free(t->ip);free(t);t=n;} }

static int seed(sqlite3 *db, struct target *targets, time_t now) {
    sqlite3_stmt *s=NULL; int rc=sqlite3_prepare_v2(db,
        "INSERT OR IGNORE INTO target(kind,ip,client_mac,ap_id,name,enabled,created_at) VALUES('ap',?,NULL,?,?,1,?)",-1,&s,NULL);
    for (;rc==SQLITE_OK && targets;targets=targets->next) {
        sqlite3_bind_text(s,1,targets->ip,-1,SQLITE_TRANSIENT);sqlite3_bind_text(s,2,targets->id,-1,SQLITE_TRANSIENT);
        sqlite3_bind_text(s,3,targets->name,-1,SQLITE_TRANSIENT);sqlite3_bind_int64(s,4,now);
        rc=sqlite3_step(s); if(rc==SQLITE_DONE)rc=sqlite3_reset(s); sqlite3_clear_bindings(s);
    }
    sqlite3_finalize(s); return rc==SQLITE_OK?0:1;
}

static int merge_legacy_aps(sqlite3 *db,const struct target *targets) {
    sqlite3_stmt *move=NULL,*remove=NULL;int rc=sqlite3_prepare_v2(db,
        "UPDATE ping_result SET target_id=(SELECT id FROM target WHERE kind='ap' AND ap_id=?) "
        "WHERE target_id IN(SELECT id FROM target WHERE kind='ap' AND length(ap_id)<>17 AND ip=?)",-1,&move,NULL);
    if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,
        "DELETE FROM target WHERE kind='ap' AND length(ap_id)<>17 AND ip=?",-1,&remove,NULL);
    for(;rc==SQLITE_OK&&targets;targets=targets->next){
        sqlite3_bind_text(move,1,targets->id,-1,SQLITE_TRANSIENT);sqlite3_bind_text(move,2,targets->ip,-1,SQLITE_TRANSIENT);
        rc=sqlite3_step(move)==SQLITE_DONE?sqlite3_reset(move):SQLITE_ERROR;sqlite3_clear_bindings(move);if(rc!=SQLITE_OK)break;
        sqlite3_bind_text(remove,1,targets->ip,-1,SQLITE_TRANSIENT);
        rc=sqlite3_step(remove)==SQLITE_DONE?sqlite3_reset(remove):SQLITE_ERROR;sqlite3_clear_bindings(remove);
    }
    sqlite3_finalize(move);sqlite3_finalize(remove);return rc==SQLITE_OK?0:1;
}

static int valid_mac(const char *mac) {
    int i;
    if (!mac || strlen(mac) != 17) return 0;
    for (i = 0; i < 17; ++i) {
        if (i % 3 == 2) { if (mac[i] != ':') return 0; }
        else if (!isxdigit((unsigned char)mac[i])) return 0;
    }
    return 1;
}

static void free_client_view(struct client_view *view) {
    size_t i;
    for (i=0;i<view->count;i++) {
        free(view->items[i].mac); free(view->items[i].ip); free(view->items[i].name);
        free(view->items[i].ap); free(view->items[i].ssid); free(view->items[i].radio);
    }
    free(view->items); memset(view,0,sizeof(*view));
}

static int client_compare(const void *left, const void *right) {
    const struct client *a=(const struct client *)left,*b=(const struct client *)right;
    return strcasecmp(a->mac,b->mac);
}

/* Parse the current client XML exactly once. The same in-memory view drives
 * discovery, address refresh, and association checks for this whole round. */
static struct client_view parse_client_view(const char *path) {
    struct client_view view={0}; FILE *f; long size; char *text=NULL,*tag;
    if(!path || !(f=fopen(path,"r")))return view;
    if(fseek(f,0,SEEK_END)||(size=ftell(f))<0||fseek(f,0,SEEK_SET)){fclose(f);return view;}
    text=malloc((size_t)size+1);
    if(!text||fread(text,1,(size_t)size,f)!=(size_t)size){free(text);fclose(f);return view;}
    fclose(f);text[size]=0;tag=text;
    while((tag=strstr(tag,"<client "))!=NULL){
        char *end=strchr(tag,'>'),*mac,*ip,*name,*ap,*ssid,*radio,*snr,*signal,*noise;struct client *grown;
        if(!end)break;
        mac=attr(tag,end,"mac");ip=attr(tag,end,"ip");name=attr(tag,end,"hostname");
        if(!name||!*name){free(name);name=attr(tag,end,"user");}
        if(!name||!*name){free(name);name=copy_text(mac);}
        ap=attr(tag,end,"ap");ssid=attr(tag,end,"ssid");radio=attr(tag,end,"radio-type");
        snr=attr(tag,end,"rssi");signal=attr(tag,end,"received-signal-strength");noise=attr(tag,end,"noise-floor");
        if(mac&&valid_mac(mac)){
            if(view.count==view.capacity){
                size_t capacity=view.capacity?view.capacity*2:128;
                grown=realloc(view.items,capacity*sizeof(*grown));
                if(!grown){free(mac);free(ip);free(name);free(text);free_client_view(&view);return view;}
                view.items=grown;view.capacity=capacity;
            }
            memset(&view.items[view.count],0,sizeof(view.items[view.count]));
            view.items[view.count].mac=mac;view.items[view.count].ip=ip;
            view.items[view.count].name=name;view.items[view.count].ap=ap;
            view.items[view.count].ssid=ssid;view.items[view.count].radio=radio;
            if(snr&&*snr){view.items[view.count].snr_db=atoi(snr);view.items[view.count].has_snr=1;}
            if(signal&&*signal){view.items[view.count].signal_dbm=atoi(signal);view.items[view.count].has_signal=1;}
            if(noise&&*noise){view.items[view.count].noise_floor_dbm=atoi(noise);view.items[view.count].has_noise=1;}
            view.count++;mac=ip=name=ap=ssid=radio=NULL;
        }
        free(mac);free(ip);free(name);free(ap);free(ssid);free(radio);free(snr);free(signal);free(noise);tag=end+1;
    }
    free(text);qsort(view.items,view.count,sizeof(*view.items),client_compare);view.valid=1;return view;
}

static int client_present(const struct client_view *view,const char *mac) {
    size_t low=0,high=view->count;
    if(!mac)return 0;
    while(low<high){size_t middle=low+(high-low)/2;int order=strcasecmp(mac,view->items[middle].mac);
        if(order==0)return 1;if(order<0)high=middle;else low=middle+1;}
    return 0;
}

static const struct client *find_client(const struct client_view *view,const char *mac) {
    size_t low=0,high=view->count;
    if(!mac)return NULL;
    while(low<high){size_t middle=low+(high-low)/2;int order=strcasecmp(mac,view->items[middle].mac);
        if(order==0)return &view->items[middle];if(order<0)high=middle;else low=middle+1;}
    return NULL;
}

/* Refresh all discovered clients with two prepared statements in the caller's
 * transaction. This replaces thousands of database opens and commits. */
static int sync_clients(sqlite3 *db,const struct client_view *view,time_t now) {
    sqlite3_stmt *insert=NULL,*update=NULL;size_t i;int rc=SQLITE_OK;
    rc=sqlite3_prepare_v2(db,"INSERT OR IGNORE INTO target(kind,ip,client_mac,ap_id,name,enabled,created_at) VALUES('client',?,?,NULL,?,1,?)",-1,&insert,NULL);
    if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,"UPDATE target SET ip=?,name=?,current_ap=?,current_ssid=?,current_radio=?,enabled=1 WHERE kind='client' AND client_mac=?",-1,&update,NULL);
    for(i=0;rc==SQLITE_OK&&i<view->count;i++){
        struct in_addr address;const struct client *client=&view->items[i];
        if(!client->ip||!client->name||inet_pton(AF_INET,client->ip,&address)!=1)continue;
        sqlite3_bind_text(insert,1,client->ip,-1,SQLITE_TRANSIENT);sqlite3_bind_text(insert,2,client->mac,-1,SQLITE_TRANSIENT);
        sqlite3_bind_text(insert,3,client->name,-1,SQLITE_TRANSIENT);sqlite3_bind_int64(insert,4,now);
        rc=sqlite3_step(insert)==SQLITE_DONE?sqlite3_reset(insert):SQLITE_ERROR;sqlite3_clear_bindings(insert);
        if(rc!=SQLITE_OK)break;
        sqlite3_bind_text(update,1,client->ip,-1,SQLITE_TRANSIENT);sqlite3_bind_text(update,2,client->name,-1,SQLITE_TRANSIENT);
        if(client->ap)sqlite3_bind_text(update,3,client->ap,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(update,3);
        if(client->ssid)sqlite3_bind_text(update,4,client->ssid,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(update,4);
        if(client->radio)sqlite3_bind_text(update,5,client->radio,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(update,5);
        sqlite3_bind_text(update,6,client->mac,-1,SQLITE_TRANSIENT);
        rc=sqlite3_step(update)==SQLITE_DONE?sqlite3_reset(update):SQLITE_ERROR;sqlite3_clear_bindings(update);
    }
    sqlite3_finalize(insert);sqlite3_finalize(update);return rc;
}

static int bind_optional_int(sqlite3_stmt *statement,int index,const char *value) {
    if(value&&*value)return sqlite3_bind_int(statement,index,atoi(value));
    return sqlite3_bind_null(statement,index);
}

/* Retain only the compact AP radio and mesh-link values needed for time-series
 * reporting.  The very large LEVEL=2 XML response is deliberately transient. */
static int ingest_ap_detail(sqlite3 *db,const char *path,time_t observed_at) {
    FILE *f;long size;char *text=NULL,*ap_tag;sqlite3_stmt *radio_put=NULL,*mesh_put=NULL;int rc=SQLITE_OK;
    if(!path||!(f=fopen(path,"r")))return SQLITE_OK;
    if(fseek(f,0,SEEK_END)||(size=ftell(f))<0||fseek(f,0,SEEK_SET)){fclose(f);return SQLITE_IOERR;}
    text=malloc((size_t)size+1);if(!text||fread(text,1,(size_t)size,f)!=(size_t)size){free(text);fclose(f);return SQLITE_NOMEM;}
    fclose(f);text[size]=0;
    rc=sqlite3_prepare_v2(db,"INSERT OR REPLACE INTO ap_radio_sample(observed_at,ap_mac,radio_id,radio_type,channel,channelization,clients,noise_floor_dbm,avg_snr_db,airtime_total_tenths,airtime_busy_tenths,airtime_rx_tenths,airtime_tx_tenths,rf_samples) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",-1,&radio_put,NULL);
    if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,"INSERT OR REPLACE INTO mesh_link_sample(observed_at,ap_mac,peer_mac,direction,radio_type,snr_db) VALUES(?,?,?,?,?,?)",-1,&mesh_put,NULL);
    ap_tag=text;
    while(rc==SQLITE_OK&&(ap_tag=strstr(ap_tag,"<ap "))!=NULL){
        char *ap_head_end=strchr(ap_tag,'>'),*ap_end,*ap_mac,*cursor;
        if(!ap_head_end||!(ap_end=strstr(ap_head_end,"</ap>")))break;
        ap_mac=attr(ap_tag,ap_head_end,"mac");
        if(ap_mac&&valid_mac(ap_mac)){
            cursor=ap_head_end+1;
            while(rc==SQLITE_OK&&(cursor=strstr(cursor,"<radio "))!=NULL&&cursor<ap_end){
                char *end=strchr(cursor,'>'),*id,*type,*channel,*width,*clients,*noise,*average,*total,*busy,*rx,*tx,*samples;
                if(!end||end>ap_end)break;
                id=attr(cursor,end,"radio-id");type=attr(cursor,end,"radio-type");channel=attr(cursor,end,"channel");
                width=attr(cursor,end,"channelization");clients=attr(cursor,end,"assoc-stas");noise=attr(cursor,end,"noisefloor");
                average=attr(cursor,end,"avg-rssi");total=attr(cursor,end,"airtime-total");busy=attr(cursor,end,"airtime-busy");
                rx=attr(cursor,end,"airtime-rx");tx=attr(cursor,end,"airtime-tx");samples=attr(cursor,end,"rf-samples");
                sqlite3_bind_int64(radio_put,1,observed_at);sqlite3_bind_text(radio_put,2,ap_mac,-1,SQLITE_TRANSIENT);
                bind_optional_int(radio_put,3,id);if(type)sqlite3_bind_text(radio_put,4,type,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(radio_put,4);
                bind_optional_int(radio_put,5,channel);bind_optional_int(radio_put,6,width);bind_optional_int(radio_put,7,clients);
                bind_optional_int(radio_put,8,noise);bind_optional_int(radio_put,9,average);bind_optional_int(radio_put,10,total);
                bind_optional_int(radio_put,11,busy);bind_optional_int(radio_put,12,rx);bind_optional_int(radio_put,13,tx);
                bind_optional_int(radio_put,14,samples);
                rc=sqlite3_step(radio_put)==SQLITE_DONE?sqlite3_reset(radio_put):SQLITE_ERROR;sqlite3_clear_bindings(radio_put);
                free(id);free(type);free(channel);free(width);free(clients);free(noise);free(average);free(total);free(busy);free(rx);free(tx);free(samples);cursor=end+1;
            }
            cursor=ap_head_end+1;
            while(rc==SQLITE_OK&&cursor<ap_end){
                char *up=strstr(cursor,"<uplink-ap "),*down=strstr(cursor,"<downlink-ap "),*tag=NULL,*end,*peer,*type,*snr;const char *direction;
                if(up&&up<ap_end&&(!down||up<down)){tag=up;direction="uplink";}else if(down&&down<ap_end){tag=down;direction="downlink";}else break;
                end=strchr(tag,'>');if(!end||end>ap_end)break;peer=attr(tag,end,"ap");type=attr(tag,end,"radio-type");snr=attr(tag,end,"rssi");
                if(peer&&valid_mac(peer)){sqlite3_bind_int64(mesh_put,1,observed_at);sqlite3_bind_text(mesh_put,2,ap_mac,-1,SQLITE_TRANSIENT);
                    sqlite3_bind_text(mesh_put,3,peer,-1,SQLITE_TRANSIENT);sqlite3_bind_text(mesh_put,4,direction,-1,SQLITE_STATIC);
                    if(type)sqlite3_bind_text(mesh_put,5,type,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(mesh_put,5);bind_optional_int(mesh_put,6,snr);
                    rc=sqlite3_step(mesh_put)==SQLITE_DONE?sqlite3_reset(mesh_put):SQLITE_ERROR;sqlite3_clear_bindings(mesh_put);}
                free(peer);free(type);free(snr);cursor=end+1;
            }
        }
        free(ap_mac);ap_tag=ap_end+5;
    }
    sqlite3_finalize(radio_put);sqlite3_finalize(mesh_put);free(text);return rc;
}

static uint64_t event_hash(const char *start,const char *end) {
    uint64_t hash=UINT64_C(1469598103934665603);const unsigned char *p=(const unsigned char *)start;
    while((const char *)p<end){hash^=*p++;hash*=UINT64_C(1099511628211);}return hash;
}

static int ingest_events(const char *path) {
    FILE *f;long size;char *text=NULL,*tag;sqlite3 *db=NULL;sqlite3_stmt *put=NULL;int rc,count=0,inserted=0;sqlite3_int64 oldest=0,newest=0;
    if(!path||!(f=fopen(path,"r")))return 1;
    if(fseek(f,0,SEEK_END)||(size=ftell(f))<0||fseek(f,0,SEEK_SET)){fclose(f);return 1;}
    text=malloc((size_t)size+1);if(!text||fread(text,1,(size_t)size,f)!=(size_t)size){free(text);fclose(f);return 1;}fclose(f);text[size]=0;
    rc=sqlite3_open(DB,&db);if(rc==SQLITE_OK)rc=schema(db);if(rc==SQLITE_OK)rc=sql(db,"BEGIN IMMEDIATE");
    if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,"INSERT OR IGNORE INTO zd_event(event_time,event_hash,severity,category,message_id,ap_mac,ap_name,client_mac,wlan,reason,details) VALUES(?,?,?,?,?,?,?,?,?,?,?)",-1,&put,NULL);
    tag=text;
    while(rc==SQLITE_OK&&(tag=strstr(tag,"<xevent "))!=NULL){
        char *end=strchr(tag,'>'),*when,*severity,*category,*message,*ap,*ap_name,*mac,*wlan,*reason,*details;char hash_text[17];
        if(!end)break;when=attr(tag,end,"time");severity=attr(tag,end,"severity");category=attr(tag,end,"c");message=attr(tag,end,"msg");
        ap=attr(tag,end,"ap");ap_name=attr(tag,end,"ap-name");mac=attr(tag,end,"mac");wlan=attr(tag,end,"wlan");reason=attr(tag,end,"reason");details=attr(tag,end,"lmsg");
        snprintf(hash_text,sizeof hash_text,"%016llx",(unsigned long long)event_hash(tag,end+1));
        if(!when||!*when){free(when);free(severity);free(category);free(message);free(ap);free(ap_name);free(mac);free(wlan);free(reason);free(details);tag=end+1;continue;}
        {sqlite3_int64 event_time=(sqlite3_int64)strtoll(when,NULL,10);if(!oldest||event_time<oldest)oldest=event_time;if(event_time>newest)newest=event_time;}
        bind_optional_int(put,1,when);sqlite3_bind_text(put,2,hash_text,-1,SQLITE_TRANSIENT);bind_optional_int(put,3,severity);
        if(category)sqlite3_bind_text(put,4,category,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,4);
        if(message)sqlite3_bind_text(put,5,message,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,5);
        if(ap&&valid_mac(ap))sqlite3_bind_text(put,6,ap,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,6);
        if(ap_name&&*ap_name)sqlite3_bind_text(put,7,ap_name,-1,SQLITE_TRANSIENT);else if(ap&&!valid_mac(ap))sqlite3_bind_text(put,7,ap,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,7);
        if(mac)sqlite3_bind_text(put,8,mac,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,8);
        if(wlan)sqlite3_bind_text(put,9,wlan,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,9);
        if(reason)sqlite3_bind_text(put,10,reason,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,10);
        if(details)sqlite3_bind_text(put,11,details,-1,SQLITE_TRANSIENT);else sqlite3_bind_null(put,11);
        if(sqlite3_step(put)==SQLITE_DONE){inserted+=sqlite3_changes(db);rc=sqlite3_reset(put);count++;}else rc=SQLITE_ERROR;sqlite3_clear_bindings(put);
        free(when);free(severity);free(category);free(message);free(ap);free(ap_name);free(mac);free(wlan);free(reason);free(details);tag=end+1;
    }
    sqlite3_finalize(put);if(rc==SQLITE_OK){char prune[160];snprintf(prune,sizeof prune,"DELETE FROM zd_event WHERE event_time<%lld",(long long)(time(NULL)-RETENTION_SECONDS));rc=sql(db,prune);}
    if(rc==SQLITE_OK)rc=sql(db,"COMMIT");else if(db&&!sqlite3_get_autocommit(db))sql(db,"ROLLBACK");
    if(rc==SQLITE_OK)printf("COUNT=%d\nINSERTED=%d\nOLDEST=%lld\nNEWEST=%lld\n",count,inserted,(long long)oldest,(long long)newest);
    free(text);sqlite3_close(db);return rc==SQLITE_OK?0:1;
}

static int event_watermark(void) {
    sqlite3 *db=NULL;sqlite3_stmt *statement=NULL;sqlite3_int64 value=0;int rc=sqlite3_open(DB,&db);
    if(rc==SQLITE_OK)rc=schema(db);if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,"SELECT coalesce(max(event_time),0) FROM zd_event",-1,&statement,NULL);
    if(rc==SQLITE_OK&&sqlite3_step(statement)==SQLITE_ROW)value=sqlite3_column_int64(statement,0);else if(rc==SQLITE_OK)rc=SQLITE_ERROR;
    sqlite3_finalize(statement);sqlite3_close(db);if(rc==SQLITE_OK)printf("%lld\n",(long long)value);return rc==SQLITE_OK?0:1;
}

static sqlite3_int64 scalar(sqlite3 *db,const char *query,sqlite3_int64 argument,int use_argument) {
    sqlite3_stmt *statement=NULL;sqlite3_int64 value=0;
    if(sqlite3_prepare_v2(db,query,-1,&statement,NULL)==SQLITE_OK){if(use_argument)sqlite3_bind_int64(statement,1,argument);if(sqlite3_step(statement)==SQLITE_ROW)value=sqlite3_column_int64(statement,0);}
    sqlite3_finalize(statement);return value;
}

static int telemetry_status_json(void) {
    sqlite3 *db=NULL;sqlite3_int64 observed=0,signals=0,radios=0,links=0,events=0;int rc=sqlite3_open_v2(DB,&db,SQLITE_OPEN_READONLY,NULL);
    if(rc!=SQLITE_OK){puts("{\"status\":\"waiting\"}");sqlite3_close(db);return 0;}
    observed=scalar(db,"SELECT coalesce(max(observed_at),0) FROM ping_result",0,0);
    signals=scalar(db,"SELECT count(*) FROM ping_result WHERE observed_at=? AND snr_db IS NOT NULL",observed,1);
    radios=scalar(db,"SELECT count(*) FROM ap_radio_sample WHERE observed_at=?",observed,1);
    links=scalar(db,"SELECT count(*) FROM mesh_link_sample WHERE observed_at=?",observed,1);
    events=scalar(db,"SELECT count(*) FROM zd_event",0,0);
    printf("{\"status\":\"ok\",\"observed_at\":%lld,\"clients_with_snr\":%lld,\"ap_radios\":%lld,\"mesh_links\":%lld,\"retained_events\":%lld}\n",(long long)observed,(long long)signals,(long long)radios,(long long)links,(long long)events);
    sqlite3_close(db);return 0;
}

static long long milliseconds(void) {
    struct timeval value;gettimeofday(&value,NULL);
    return (long long)value.tv_sec*1000+value.tv_usec/1000;
}

static uint16_t icmp_checksum(const void *data,size_t length) {
    const uint16_t *word=(const uint16_t *)data;uint32_t sum=0;
    while(length>1){sum+=*word++;length-=2;}
    if(length)sum+=*(const unsigned char *)word;
    while(sum>>16)sum=(sum&0xffff)+(sum>>16);
    return (uint16_t)~sum;
}

/* Probe at most 512 targets at once. Ten one-second timeout windows cover
 * 5,000 targets, leaving ample room in a 30-second collection interval. */
static int parallel_ping(struct measurement *items,size_t count) {
    size_t *indices=NULL,ping_count=0,i,start;int sock,receive_buffer=4*1024*1024;
    uint16_t identifier=(uint16_t)(((unsigned)getpid()^(unsigned)time(NULL))&0xffff);
    indices=malloc(count*sizeof(*indices));if(!indices&&count)return -1;
    for(i=0;i<count;i++)if(items[i].pingable)indices[ping_count++]=i;
    sock=socket(AF_INET,SOCK_RAW,IPPROTO_ICMP);
    if(sock<0){perror("raw ICMP socket");free(indices);return -1;}
    setsockopt(sock,SOL_SOCKET,SO_RCVBUF,&receive_buffer,sizeof(receive_buffer));
    for(start=0;start<ping_count;start+=PING_BATCH_SIZE){
        size_t end=start+PING_BATCH_SIZE;int pending=0;long long deadline=0;
        if(end>ping_count)end=ping_count;
        for(i=start;i<end;i++){
            struct measurement *item=&items[indices[i]];struct echo_packet packet;struct sockaddr_in destination;
            memset(&packet,0,sizeof(packet));packet.header.type=ICMP_ECHO;
            packet.header.un.echo.id=htons(identifier);packet.header.un.echo.sequence=htons((uint16_t)(i+1));
            packet.magic=htonl(ICMP_MAGIC);packet.index=htonl((uint32_t)i);
            packet.header.checksum=icmp_checksum(&packet,sizeof(packet));
            memset(&destination,0,sizeof(destination));destination.sin_family=AF_INET;destination.sin_addr=item->address;
            item->sent_ms=milliseconds();item->state="timeout";item->rtt_ms=TIMEOUT_MS;
            if(sendto(sock,&packet,sizeof(packet),0,(struct sockaddr *)&destination,sizeof(destination))==(ssize_t)sizeof(packet)){
                long long item_deadline=item->sent_ms+TIMEOUT_MS;if(item_deadline>deadline)deadline=item_deadline;pending++;
            }
        }
        while(pending>0){
            unsigned char buffer[2048];struct sockaddr_in source;socklen_t source_length=sizeof(source);
            struct pollfd descriptor;long long current=milliseconds();int wait,ready;ssize_t length;
            if(current>=deadline)break;wait=(int)(deadline-current);
            descriptor.fd=sock;descriptor.events=POLLIN;descriptor.revents=0;
            ready=poll(&descriptor,1,wait);if(ready<=0){if(ready<0&&errno==EINTR)continue;break;}
            length=recvfrom(sock,buffer,sizeof(buffer),0,(struct sockaddr *)&source,&source_length);
            if(length>=(ssize_t)(sizeof(struct iphdr)+sizeof(struct echo_packet))){
                struct iphdr *ip=(struct iphdr *)buffer;size_t offset=(size_t)ip->ihl*4;
                if(offset+sizeof(struct echo_packet)<=(size_t)length){
                    struct echo_packet *packet=(struct echo_packet *)(buffer+offset);uint32_t position=ntohl(packet->index);
                    if(packet->header.type==ICMP_ECHOREPLY&&ntohs(packet->header.un.echo.id)==identifier
                        &&ntohl(packet->magic)==ICMP_MAGIC&&position>=start&&position<end){
                        struct measurement *item=&items[indices[position]];
                        if(!item->responded&&source.sin_addr.s_addr==item->address.s_addr){
                            long long elapsed=milliseconds()-item->sent_ms;if(elapsed<0)elapsed=0;if(elapsed>TIMEOUT_MS)elapsed=TIMEOUT_MS;
                            item->rtt_ms=(int)elapsed;item->responded=1;item->state="ok";pending--;
                        }
                    }
                }
            }
        }
    }
    close(sock);free(indices);return 0;
}

static int load_measurements(sqlite3 *db,const struct client_view *clients,struct measurement **result,size_t *result_count) {
    sqlite3_stmt *query=NULL;struct measurement *items=NULL;size_t count=0,capacity=0;int rc;
    rc=sqlite3_prepare_v2(db,"SELECT id,kind,ip,client_mac FROM target WHERE enabled=1 ORDER BY id",-1,&query,NULL);
    while(rc==SQLITE_OK){
        int step=sqlite3_step(query);
        if(step!=SQLITE_ROW){rc=step;break;}
        {
        const char *kind=(const char *)sqlite3_column_text(query,1),*ip=(const char *)sqlite3_column_text(query,2),*mac=(const char *)sqlite3_column_text(query,3);
        struct measurement *grown,*item;
        if(count==capacity){size_t next=capacity?capacity*2:256;grown=realloc(items,next*sizeof(*grown));if(!grown){rc=SQLITE_NOMEM;break;}items=grown;capacity=next;}
        item=&items[count++];memset(item,0,sizeof(*item));item->id=sqlite3_column_int64(query,0);item->rtt_ms=TIMEOUT_MS;
        if(kind&&strcmp(kind,"client")==0&&!clients->valid)item->state="unknown";
        else if(kind&&strcmp(kind,"client")==0&&!client_present(clients,mac))item->state="not_associated";
        else if(ip&&inet_pton(AF_INET,ip,&item->address)==1){item->state="timeout";item->pingable=1;}
        else item->state="unknown";
        if(kind&&strcmp(kind,"client")==0){const struct client *client=find_client(clients,mac);if(client){
            item->snr_db=client->snr_db;item->signal_dbm=client->signal_dbm;item->noise_floor_dbm=client->noise_floor_dbm;
            item->has_snr=client->has_snr;item->has_signal=client->has_signal;item->has_noise=client->has_noise;}}
        }
    }
    sqlite3_finalize(query);if(rc!=SQLITE_DONE){free(items);return rc;}*result=items;*result_count=count;return SQLITE_OK;
}

static int tick(const char *client_xml,const char *ap_detail_xml) {
    sqlite3 *db=NULL;sqlite3_stmt *put=NULL;struct target *aps=read_aps();struct client_view clients=parse_client_view(client_xml);
    struct measurement *items=NULL;size_t count=0,i;time_t now=time(NULL);const char *stage="open database";int rc=sqlite3_open(DB,&db);
    if(rc==SQLITE_OK){stage="initialize schema";rc=schema(db);}
    if(rc==SQLITE_OK){stage="begin target sync";rc=sql(db,"BEGIN IMMEDIATE");}
    if(rc==SQLITE_OK){stage="sync access points";rc=seed(db,aps,now);}
    if(rc==SQLITE_OK){stage="merge legacy access points";rc=merge_legacy_aps(db,aps);}
    free_targets(aps);
    if(rc==SQLITE_OK&&clients.valid){stage="sync clients";rc=sync_clients(db,&clients,now);}
    if(rc==SQLITE_OK){stage="commit target sync";rc=sql(db,"COMMIT");}else if(db&&!sqlite3_get_autocommit(db))sql(db,"ROLLBACK");
    if(rc==SQLITE_OK){stage="load targets";rc=load_measurements(db,&clients,&items,&count);}
    if(rc==SQLITE_OK){stage="send ICMP probes";if(parallel_ping(items,count)!=0)rc=SQLITE_IOERR;}
    if(rc==SQLITE_OK){stage="begin result write";rc=sql(db,"BEGIN IMMEDIATE");}
    if(rc==SQLITE_OK){stage="prepare result write";rc=sqlite3_prepare_v2(db,"INSERT INTO ping_result(observed_at,target_id,rtt_ms,responded,state,snr_db,signal_dbm,noise_floor_dbm) VALUES(?,?,?,?,?,?,?,?)",-1,&put,NULL);}
    if(rc==SQLITE_OK)stage="write results";
    for(i=0;rc==SQLITE_OK&&i<count;i++){
        sqlite3_bind_int64(put,1,now);sqlite3_bind_int64(put,2,items[i].id);sqlite3_bind_int(put,3,items[i].rtt_ms);
        sqlite3_bind_int(put,4,items[i].responded);sqlite3_bind_text(put,5,items[i].state,-1,SQLITE_STATIC);
        if(items[i].has_snr)sqlite3_bind_int(put,6,items[i].snr_db);else sqlite3_bind_null(put,6);
        if(items[i].has_signal)sqlite3_bind_int(put,7,items[i].signal_dbm);else sqlite3_bind_null(put,7);
        if(items[i].has_noise)sqlite3_bind_int(put,8,items[i].noise_floor_dbm);else sqlite3_bind_null(put,8);
        rc=sqlite3_step(put)==SQLITE_DONE?sqlite3_reset(put):SQLITE_ERROR;sqlite3_clear_bindings(put);
    }
    sqlite3_finalize(put);
    if(rc==SQLITE_OK){stage="store AP telemetry";rc=ingest_ap_detail(db,ap_detail_xml,now);}
    if(rc==SQLITE_OK){char prune[512];stage="prune results";snprintf(prune,sizeof(prune),"DELETE FROM ping_result WHERE observed_at<%lld;DELETE FROM ap_radio_sample WHERE observed_at<%lld;DELETE FROM mesh_link_sample WHERE observed_at<%lld",(long long)(now-RETENTION_SECONDS),(long long)(now-RETENTION_SECONDS),(long long)(now-RETENTION_SECONDS));rc=sql(db,prune);}
    if(rc==SQLITE_OK){stage="commit results";rc=sql(db,"COMMIT");}else if(db&&!sqlite3_get_autocommit(db))sql(db,"ROLLBACK");
    if(rc!=SQLITE_OK)fprintf(stderr,"ping round (%s, rc=%d): %s\n",stage,rc,db?sqlite3_errmsg(db):"database unavailable");
    free(items);free_client_view(&clients);sqlite3_close(db);return rc==SQLITE_OK?0:1;
}

static int add_client(const char *mac, const char *ip, const char *name) {
    sqlite3 *db = NULL; sqlite3_stmt *s = NULL; struct in_addr parsed; int rc;
    if (!valid_mac(mac) || inet_pton(AF_INET, ip, &parsed) != 1 || !name || !*name) return 2;
    rc = sqlite3_open(DB, &db);
    if (rc == SQLITE_OK) rc = schema(db);
    /* SQLite 3.7.17 in the guest predates INSERT ... ON CONFLICT DO UPDATE. */
    if (rc == SQLITE_OK) rc = sqlite3_prepare_v2(db,
        "UPDATE target SET ip=?,name=?,enabled=1 WHERE kind='client' AND client_mac=?", -1, &s, NULL);
    if (rc == SQLITE_OK) {
        sqlite3_bind_text(s,1,ip,-1,SQLITE_TRANSIENT); sqlite3_bind_text(s,2,name,-1,SQLITE_TRANSIENT);
        sqlite3_bind_text(s,3,mac,-1,SQLITE_TRANSIENT);
        rc = sqlite3_step(s) == SQLITE_DONE ? SQLITE_OK : SQLITE_ERROR;
        if (rc == SQLITE_OK && sqlite3_changes(db) == 0) {
            sqlite3_finalize(s); s = NULL;
            rc = sqlite3_prepare_v2(db,
                "INSERT INTO target(kind,ip,client_mac,ap_id,name,enabled,created_at) VALUES('client',?,?,NULL,?,1,?)", -1, &s, NULL);
            if (rc == SQLITE_OK) {
                sqlite3_bind_text(s,1,ip,-1,SQLITE_TRANSIENT); sqlite3_bind_text(s,2,mac,-1,SQLITE_TRANSIENT);
                sqlite3_bind_text(s,3,name,-1,SQLITE_TRANSIENT); sqlite3_bind_int64(s,4,time(NULL));
                rc = sqlite3_step(s) == SQLITE_DONE ? SQLITE_OK : SQLITE_ERROR;
            }
        }
    }
    if (rc != SQLITE_OK) fprintf(stderr, "add-client: %s\n", sqlite3_errmsg(db));
    sqlite3_finalize(s); sqlite3_close(db); return rc == SQLITE_OK ? 0 : 1;
}

static int set_enabled(const char *id_text, const char *enabled_text) {
    sqlite3 *db=NULL; sqlite3_stmt *s=NULL; char *end; long id=strtol(id_text,&end,10); int enabled;
    if (!id_text || !*id_text || *end || id<1 || !enabled_text || (strcmp(enabled_text,"0") && strcmp(enabled_text,"1"))) return 2;
    enabled=enabled_text[0]-'0'; if(sqlite3_open(DB,&db)!=SQLITE_OK)return 1; if(schema(db)!=0){sqlite3_close(db);return 1;}
    if(sqlite3_prepare_v2(db,"UPDATE target SET enabled=? WHERE id=?",-1,&s,NULL)!=SQLITE_OK){sqlite3_close(db);return 1;}
    sqlite3_bind_int(s,1,enabled);sqlite3_bind_int64(s,2,id);int ok=sqlite3_step(s)==SQLITE_DONE && sqlite3_changes(db)==1;sqlite3_finalize(s);sqlite3_close(db);return ok?0:1;
}

/* Render display aggregates directly from raw samples.  They are never
 * retained as a second history: changing a viewport simply changes how the
 * same 30-day observations are grouped. */
static void series(sqlite3 *db, sqlite3_int64 target, sqlite3_int64 start, int bucket, int bins) {
    sqlite3_stmt *s=NULL; int *values=NULL,*count=NULL,*fail=NULL,i,j;
    values=calloc((size_t)bins*MAX_BUCKET_SAMPLES,sizeof(*values));
    count=calloc((size_t)bins,sizeof(*count)); fail=calloc((size_t)bins,sizeof(*fail));
    if(!values||!count||!fail){fputs("[]",stdout);free(values);free(count);free(fail);return;}
    if(sqlite3_prepare_v2(db,"SELECT observed_at,rtt_ms,responded FROM ping_result WHERE target_id=? AND observed_at>=? AND state IN('ok','timeout') ORDER BY observed_at,rtt_ms",-1,&s,NULL)==SQLITE_OK){
        sqlite3_bind_int64(s,1,target);sqlite3_bind_int64(s,2,start);
        while(sqlite3_step(s)==SQLITE_ROW){int b=(int)((sqlite3_column_int64(s,0)-start)/bucket);if(b>=0&&b<bins&&count[b]<MAX_BUCKET_SAMPLES){values[b*MAX_BUCKET_SAMPLES+count[b]++]=sqlite3_column_int(s,1);if(!sqlite3_column_int(s,2))fail[b]++;}}
    }
    sqlite3_finalize(s);putchar('[');for(i=0;i<bins;i++){if(i)putchar(',');if(!count[i])fputs("[0,0,null,null]",stdout);else{for(j=0;j<count[i];j++){int k;for(k=j+1;k<count[i];k++)if(values[i*MAX_BUCKET_SAMPLES+k]<values[i*MAX_BUCKET_SAMPLES+j]){int x=values[i*MAX_BUCKET_SAMPLES+j];values[i*MAX_BUCKET_SAMPLES+j]=values[i*MAX_BUCKET_SAMPLES+k];values[i*MAX_BUCKET_SAMPLES+k]=x;}}printf("[%d,%d,%d,%d]",count[i],fail[i],values[i*MAX_BUCKET_SAMPLES+(count[i]-1)/2],values[i*MAX_BUCKET_SAMPLES+(count[i]*99+99)/100-1]);}}putchar(']');
    free(values);free(count);free(fail);
}

static void history(sqlite3 *db, sqlite3_int64 target, sqlite3_int64 start) { fputs("\"hours\":",stdout);series(db,target,start,3600,HOURS); }
static void timeline(sqlite3 *db, sqlite3_int64 target, time_t now) {
    struct spec { const char *name; int span,bucket; } specs[]={{"1h",3600,60},{"24h",86400,600},{"7d",604800,3600},{"30d",2592000,21600}};
    int i;fputs("\"timeline\":{",stdout);for(i=0;i<4;i++){/* ceil keeps the last bucket open through 'now' */sqlite3_int64 start=(((sqlite3_int64)now-specs[i].span+specs[i].bucket-1)/specs[i].bucket)*specs[i].bucket;if(i)putchar(',');printf("\"%s\":{\"start\":%lld,\"bucket\":%d,\"points\":",specs[i].name,(long long)start,specs[i].bucket);series(db,target,start,specs[i].bucket,specs[i].span/specs[i].bucket);putchar('}');}putchar('}');
}

static int pings_json(void) {
    sqlite3*db=NULL;sqlite3_stmt*s=NULL;time_t now=time(NULL);sqlite3_int64 start=((sqlite3_int64)now-23*3600)/3600*3600;int rc=sqlite3_open_v2(DB,&db,SQLITE_OPEN_READONLY,NULL),first=1;
    if(rc!=SQLITE_OK){puts("{\"status\":\"waiting\",\"targets\":[]}");sqlite3_close(db);return 0;}
    rc=sqlite3_prepare_v2(db,"SELECT t.id,t.kind,t.ip,t.client_mac,t.ap_id,t.name,t.enabled,coalesce(sum(p.state IN('ok','timeout')),0),coalesce(sum(p.state='timeout'),0),coalesce(sum(p.state='not_associated'),0),coalesce(sum(p.state='unknown'),0),coalesce(max(p.observed_at),0),coalesce((SELECT count(*) FROM ping_result h WHERE h.target_id=t.id AND h.state IN('ok','timeout')),0),coalesce((SELECT count(*) FROM ping_result h WHERE h.target_id=t.id AND h.responded=1),0),coalesce((SELECT max(observed_at) FROM ping_result h WHERE h.target_id=t.id AND h.responded=1),0) FROM target t LEFT JOIN ping_result p ON p.target_id=t.id AND p.observed_at>=? GROUP BY t.id ORDER BY t.kind,t.name",-1,&s,NULL);
    if(rc!=SQLITE_OK){puts("{\"status\":\"error\",\"reason\":\"query_failed\"}");sqlite3_close(db);return 0;}
    sqlite3_bind_int64(s,1,start);printf("{\"status\":\"ok\",\"window_start\":%lld,\"window_end\":%lld,\"targets\":[",(long long)start,(long long)now);
    while(sqlite3_step(s)==SQLITE_ROW){if(!first)putchar(',');first=0;printf("{\"id\":%lld,\"kind\":",(long long)sqlite3_column_int64(s,0));json((const char*)sqlite3_column_text(s,1));fputs(",\"ip\":",stdout);json((const char*)sqlite3_column_text(s,2));fputs(",\"client_mac\":",stdout);json((const char*)sqlite3_column_text(s,3));fputs(",\"ap_id\":",stdout);json((const char*)sqlite3_column_text(s,4));fputs(",\"name\":",stdout);json((const char*)sqlite3_column_text(s,5));printf(",\"enabled\":%s,\"samples\":%lld,\"failures\":%lld,\"not_associated\":%lld,\"unknown\":%lld,\"last_observed_at\":%lld,\"retained_attempts\":%lld,\"successful_replies\":%lld,\"last_response_at\":%lld,",sqlite3_column_int(s,6)?"true":"false",(long long)sqlite3_column_int64(s,7),(long long)sqlite3_column_int64(s,8),(long long)sqlite3_column_int64(s,9),(long long)sqlite3_column_int64(s,10),(long long)sqlite3_column_int64(s,11),(long long)sqlite3_column_int64(s,12),(long long)sqlite3_column_int64(s,13),(long long)sqlite3_column_int64(s,14));history(db,sqlite3_column_int64(s,0),start);putchar(',');timeline(db,sqlite3_column_int64(s,0),now);putchar('}');}
    puts("]}");sqlite3_finalize(s);sqlite3_close(db);return 0;
}

static long snapshot_time(const char *name) { char *end;long v=strtol(name,&end,10);return (v>0&&end&&(!strcmp(end,"-ap.xml")||!strcmp(end,"-ap.xml.gz")))?v:0; }
static int long_compare(const void *a, const void *b) { long x=*(const long *)a,y=*(const long *)b;return x<y?-1:x>y; }
static int complete_snapshot(long time,const char *extension) {
    char ap[256], client[256], mesh[256];
    snprintf(ap,sizeof(ap),"%s/%ld-ap.xml%s",SNAPSHOT_DIR,time,extension);
    snprintf(client,sizeof(client),"%s/%ld-client.xml%s",SNAPSHOT_DIR,time,extension);
    snprintf(mesh,sizeof(mesh),"%s/%ld-mesh.xml%s",SNAPSHOT_DIR,time,extension);
    return access(ap,R_OK)==0 && access(client,R_OK)==0 && access(mesh,R_OK)==0;
}
static int snapshot_times(void) {
    DIR*d=opendir(SNAPSHOT_DIR);struct dirent*e;long *times=NULL;size_t n=0,capacity=0,i;
    if(!d)return 0;
    while((e=readdir(d))){
        long t=snapshot_time(e->d_name);long *grown;
        if(!t||(!complete_snapshot(t,".gz")&&!complete_snapshot(t,"")))continue;
        if(n==capacity){capacity=capacity?capacity*2:4096;grown=realloc(times,capacity*sizeof(*times));if(!grown){free(times);closedir(d);return 1;}times=grown;}
        times[n++]=t;
    }
    closedir(d);qsort(times,n,sizeof(*times),long_compare);
    for(i=0;i<n;i++)if(!i||times[i]!=times[i-1])printf("%ld\n",times[i]);
    free(times);return 0;
}
static int prune_snapshots(void) { DIR*d=opendir(SNAPSHOT_DIR);struct dirent*e;long cut=(long)time(NULL)-RETENTION_SECONDS;if(!d)return 0;while((e=readdir(d))){char *dash=strchr(e->d_name,'-');long t=strtol(e->d_name,NULL,10);if(dash&&t>0&&t<cut){char p[512];snprintf(p,sizeof(p),"%s/%s",SNAPSHOT_DIR,e->d_name);unlink(p);}}closedir(d);return 0; }
int main(int argc,char**argv){
    if(argc==2&&strcmp(argv[1],"tick")==0)return tick(NULL,NULL);
    if(argc==3&&strcmp(argv[1],"tick")==0)return tick(argv[2],NULL);
    if(argc==4&&strcmp(argv[1],"tick")==0)return tick(argv[2],argv[3]);
    if(argc==3&&strcmp(argv[1],"ingest-events")==0)return ingest_events(argv[2]);
    if(argc==2&&strcmp(argv[1],"event-watermark")==0)return event_watermark();
    if(argc==2&&strcmp(argv[1],"telemetry-status-json")==0)return telemetry_status_json();
    if(argc==4&&strcmp(argv[1],"add-client")==0)return add_client(argv[2],argv[3],"Client");
    if(argc==5&&strcmp(argv[1],"add-client")==0)return add_client(argv[2],argv[3],argv[4]);
    if(argc==4&&strcmp(argv[1],"set-enabled")==0)return set_enabled(argv[2],argv[3]);
    if(argc==2&&strcmp(argv[1],"pings-json")==0)return pings_json();
    if(argc==2&&strcmp(argv[1],"snapshot-times")==0)return snapshot_times();
    if(argc==2&&strcmp(argv[1],"prune-snapshots")==0)return prune_snapshots();
    fprintf(stderr,"Usage: %s {tick [client.xml [ap-detail.xml]]|ingest-events events.xml|add-client MAC IP NAME|set-enabled ID 0|1|pings-json|snapshot-times|prune-snapshots}\n",argv[0]);return 2;
}
