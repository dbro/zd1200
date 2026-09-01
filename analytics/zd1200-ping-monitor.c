/* Local ping results and raw-network-snapshot index for virtual ZD1200.
 *
 * Network XML is intentionally never parsed or normalized here. The shell
 * collector writes the three stock responses as timestamped files; this helper
 * stores ping results and publishes only a directory index for the browser.
 */
#include <sqlite3.h>
#include <arpa/inet.h>
#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define DB "/writable/zd1200-ping-monitor/pings.db"
#define AP_LIST "/writable/etc/airespider/ap-list.xml"
#define SNAPSHOT_DIR "/writable/zd1200-ping-monitor/snapshots"
#define RETENTION_SECONDS (30 * 24 * 60 * 60)
#define TIMEOUT_MS 1000
#define HOURS 24
#define MAX_BUCKET_SAMPLES 800

struct target { char *id, *name, *ip; struct target *next; };
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
        "enabled INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL"
        ");"
        "CREATE TABLE IF NOT EXISTS ping_result("
        "id INTEGER PRIMARY KEY,observed_at INTEGER NOT NULL,target_id INTEGER NOT NULL,"
        "rtt_ms INTEGER NOT NULL CHECK(rtt_ms>=0 AND rtt_ms<=1000),"
        "responded INTEGER NOT NULL CHECK(responded IN(0,1)),"
        "state TEXT NOT NULL DEFAULT 'timeout' CHECK(state IN('ok','timeout','not_associated','unknown')),"
        "FOREIGN KEY(target_id) REFERENCES target(id));"
        "CREATE INDEX IF NOT EXISTS ping_result_target_time "
        "ON ping_result(target_id,observed_at);") != SQLITE_OK) return SQLITE_ERROR;

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
        if (!end) break; id=attr(tag,end,"id"); name=attr(tag,end,"name"); ip=attr(tag,end,"ip");
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

static int valid_mac(const char *mac) {
    int i;
    if (!mac || strlen(mac) != 17) return 0;
    for (i = 0; i < 17; ++i) {
        if (i % 3 == 2) { if (mac[i] != ':') return 0; }
        else if (!isxdigit((unsigned char)mac[i])) return 0;
    }
    return 1;
}

static int xml_has_client(const char *path, const char *mac) {
    FILE *f; long size; char *text, *tag; int found = 0;
    if (!path || !valid_mac(mac) || !(f = fopen(path, "r"))) return 0;
    if (fseek(f, 0, SEEK_END) || (size = ftell(f)) < 0 || fseek(f, 0, SEEK_SET)) { fclose(f); return 0; }
    text = malloc((size_t)size + 1);
    if (!text || fread(text, 1, (size_t)size, f) != (size_t)size) { free(text); fclose(f); return 0; }
    fclose(f); text[size] = 0; tag = text;
    while ((tag = strstr(tag, "<client ")) != NULL) {
        char *end = strchr(tag, '>'); char *current;
        if (!end) break;
        current = attr(tag, end, "mac");
        if (current && strcasecmp(current, mac) == 0) found = 1;
        free(current);
        if (found) break;
        tag = end + 1;
    }
    free(text); return found;
}

static int ping(const char *ip, int *rtt) {
    int fd[2], status; pid_t child; char out[2048],*m; ssize_t used=0,n;
    struct in_addr a; if(inet_pton(AF_INET,ip,&a)!=1 || pipe(fd)) return 0;
    child=fork(); if(child==0) { dup2(fd[1],1);dup2(fd[1],2);close(fd[0]);close(fd[1]);
        execl("/bin/ping","ping","-c","1","-W","1",ip,(char*)NULL);
        execl("/bin/busybox","busybox","ping","-c","1","-W","1",ip,(char*)NULL); _exit(127); }
    close(fd[1]); while(used<(ssize_t)sizeof(out)-1 && (n=read(fd[0],out+used,sizeof(out)-1-used))>0)used+=n;
    close(fd[0]);out[used]=0;waitpid(child,&status,0);m=strstr(out,"time=");
    if(m && WIFEXITED(status) && WEXITSTATUS(status)==0) { double v=strtod(m+5,NULL);if(v<0)v=0;if(v>TIMEOUT_MS)v=TIMEOUT_MS;*rtt=(int)(v+.5);return 1; }
    *rtt=TIMEOUT_MS;return 0;
}

/* A target is discovered from the controller's current client view.  The
 * address is deliberately treated as a refreshable observation: the MAC is
 * what identifies the client to this monitor. */
static void sync_clients(const char *path) {
    FILE *f; long size; char *text, *tag;
    if (!path || !(f = fopen(path, "r"))) return;
    if (fseek(f,0,SEEK_END) || (size=ftell(f))<0 || fseek(f,0,SEEK_SET)) { fclose(f); return; }
    text=malloc((size_t)size+1);
    if (!text || fread(text,1,(size_t)size,f)!=(size_t)size) { free(text); fclose(f); return; }
    fclose(f); text[size]=0; tag=text;
    while ((tag=strstr(tag,"<client ")) != NULL) {
        char *end=strchr(tag,'>'), *mac, *ip, *name;
        if (!end) break;
        mac=attr(tag,end,"mac"); ip=attr(tag,end,"ip"); name=attr(tag,end,"hostname");
        if (!name || !*name) { free(name); name=attr(tag,end,"user"); }
        if (!name || !*name) { free(name); name=copy_text(mac); }
        if (mac && ip && name) add_client(mac,ip,name);
        free(mac); free(ip); free(name); tag=end+1;
    }
    free(text);
}

static int tick(const char *client_xml) {
    sqlite3 *db=NULL;sqlite3_stmt *q=NULL,*put=NULL;struct target *aps=read_aps();time_t now=time(NULL);int rc;
    sync_clients(client_xml);
    rc=sqlite3_open(DB,&db);
    if(rc==SQLITE_OK)rc=schema(db);if(rc==SQLITE_OK)rc=seed(db,aps,now);free_targets(aps);
    if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,"SELECT id,kind,ip,client_mac FROM target WHERE enabled=1 ORDER BY id",-1,&q,NULL);
    if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,"INSERT INTO ping_result(observed_at,target_id,rtt_ms,responded,state) VALUES(?,?,?,?,?)",-1,&put,NULL);
    while(rc==SQLITE_OK && sqlite3_step(q)==SQLITE_ROW) { int rtt=TIMEOUT_MS,ok=0; const char *state="timeout";
        const char *kind=(const char*)sqlite3_column_text(q,1), *ip=(const char*)sqlite3_column_text(q,2), *mac=(const char*)sqlite3_column_text(q,3);
        /* Never infer a client's presence from an old snapshot.  A missing
         * current controller response is an unknown measurement, distinct
         * from both ICMP loss and a confirmed non-associated client. */
        if(kind && strcmp(kind,"client")==0 && (!client_xml || !*client_xml)) state="unknown";
        else if(kind && strcmp(kind,"client")==0 && !xml_has_client(client_xml,mac)) state="not_associated";
        else { ok=ping(ip,&rtt); state=ok?"ok":"timeout"; }
        sqlite3_bind_int64(put,1,now);sqlite3_bind_int64(put,2,sqlite3_column_int64(q,0));sqlite3_bind_int(put,3,rtt);sqlite3_bind_int(put,4,ok);sqlite3_bind_text(put,5,state,-1,SQLITE_STATIC);
        rc=sqlite3_step(put);if(rc==SQLITE_DONE)rc=sqlite3_reset(put);sqlite3_clear_bindings(put); }
    if(rc==SQLITE_OK || rc==SQLITE_DONE) { char prune[160];snprintf(prune,sizeof(prune),"DELETE FROM ping_result WHERE observed_at<%lld",(long long)(now-RETENTION_SECONDS));sql(db,prune);rc=SQLITE_OK; }
    sqlite3_finalize(put);sqlite3_finalize(q);sqlite3_close(db);return rc==SQLITE_OK?0:1;
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

static long snapshot_time(const char *name) { char *end;long v=strtol(name,&end,10);return (v>0 && end && strcmp(end,"-ap.xml")==0)?v:0; }
static int long_compare(const void *a, const void *b) { long x=*(const long *)a,y=*(const long *)b;return x<y?-1:x>y; }
static int complete_snapshot(long time) {
    char ap[256], client[256], mesh[256];
    snprintf(ap,sizeof(ap),"%s/%ld-ap.xml",SNAPSHOT_DIR,time);
    snprintf(client,sizeof(client),"%s/%ld-client.xml",SNAPSHOT_DIR,time);
    snprintf(mesh,sizeof(mesh),"%s/%ld-mesh.xml",SNAPSHOT_DIR,time);
    return access(ap,R_OK)==0 && access(client,R_OK)==0 && access(mesh,R_OK)==0;
}
static int snapshot_index(void) {
    DIR*d=opendir(SNAPSHOT_DIR);struct dirent*e;long *times;int n=0,i;if(!d){puts("{\"status\":\"ok\",\"snapshots\":[]}");return 0;}times=calloc(50000,sizeof(*times));if(!times){closedir(d);return 1;}
    while((e=readdir(d))&&n<50000){long t=snapshot_time(e->d_name);if(t&&complete_snapshot(t))times[n++]=t;}closedir(d);qsort(times,n,sizeof(*times),long_compare);
    fputs("{\"status\":\"ok\",\"snapshots\":[",stdout);for(i=0;i<n;i++){if(i)putchar(',');printf("{\"observed_at\":%ld,\"ap\":\"%ld-ap.xml\",\"client\":\"%ld-client.xml\",\"mesh\":\"%ld-mesh.xml\"}",times[i],times[i],times[i],times[i]);}puts("]}");free(times);return 0;
}
static int prune_snapshots(void) { DIR*d=opendir(SNAPSHOT_DIR);struct dirent*e;long cut=(long)time(NULL)-RETENTION_SECONDS;if(!d)return 0;while((e=readdir(d))){char *dash=strchr(e->d_name,'-');long t=strtol(e->d_name,NULL,10);if(dash&&t>0&&t<cut){char p[512];snprintf(p,sizeof(p),"%s/%s",SNAPSHOT_DIR,e->d_name);unlink(p);}}closedir(d);return 0; }
int main(int argc,char**argv){
    if(argc==2&&strcmp(argv[1],"tick")==0)return tick(NULL);
    if(argc==3&&strcmp(argv[1],"tick")==0)return tick(argv[2]);
    if(argc==4&&strcmp(argv[1],"add-client")==0)return add_client(argv[2],argv[3],"Client");
    if(argc==5&&strcmp(argv[1],"add-client")==0)return add_client(argv[2],argv[3],argv[4]);
    if(argc==4&&strcmp(argv[1],"set-enabled")==0)return set_enabled(argv[2],argv[3]);
    if(argc==2&&strcmp(argv[1],"pings-json")==0)return pings_json();
    if(argc==2&&strcmp(argv[1],"snapshot-index")==0)return snapshot_index();
    if(argc==2&&strcmp(argv[1],"prune-snapshots")==0)return prune_snapshots();
    fprintf(stderr,"Usage: %s {tick [client.xml]|add-client MAC IP NAME|set-enabled ID 0|1|pings-json|snapshot-index|prune-snapshots}\n",argv[0]);return 2;
}
