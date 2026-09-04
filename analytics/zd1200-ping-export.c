/* Export full-precision SQLite ping history into browser-facing daily chunks.
 * The format is versioned, target-major, and contains no derived metrics.
 */
#include <sqlite3.h>
#include <ctype.h>
#include <dirent.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#define DB "/writable/zd1200-ping-monitor/pings.db"
#define DAILY_DIR "/writable/zd1200-ping-monitor/daily"
#define HEADER_SIZE 576U
#define FORMAT_VERSION 1U
#define CODE_COUNT 254U
#define TIMEOUT_MS 2000U

struct round_list { uint32_t *items; size_t count, capacity; };
struct export_target { sqlite3_int64 id; unsigned char mac[6]; char mac_text[18]; };
struct target_list { struct export_target *items; size_t count, capacity; };
struct period_file { unsigned long start; long long bytes, revision; char name[64]; };

static void json(const char *s) {
    const unsigned char *p=(const unsigned char *)(s?s:"");
    putchar('"');
    for(;*p;p++) switch(*p) {
    case '"': fputs("\\\"",stdout);break;
    case '\\': fputs("\\\\",stdout);break;
    case '\n': fputs("\\n",stdout);break;
    case '\r': fputs("\\r",stdout);break;
    case '\t': fputs("\\t",stdout);break;
    default: if(*p<0x20)printf("\\u%04x",*p);else putchar(*p);
    }
    putchar('"');
}

static int hex_value(int value) {
    if(value>='0'&&value<='9')return value-'0';
    value=tolower((unsigned char)value);
    return value>='a'&&value<='f'?value-'a'+10:-1;
}

static int parse_mac(const char *text,unsigned char out[6],char canonical[18]) {
    int i,high,low;
    if(!text||strlen(text)!=17)return 0;
    for(i=0;i<6;i++) {
        if(i&&text[i*3-1]!=':')return 0;
        high=hex_value(text[i*3]);low=hex_value(text[i*3+1]);
        if(high<0||low<0)return 0;
        out[i]=(unsigned char)((high<<4)|low);
    }
    snprintf(canonical,18,"%02x:%02x:%02x:%02x:%02x:%02x",
        out[0],out[1],out[2],out[3],out[4],out[5]);
    return 1;
}

static int round_append(struct round_list *list,uint32_t value) {
    uint32_t *grown;size_t capacity;
    if(list->count==list->capacity) {
        capacity=list->capacity?list->capacity*2:256;
        grown=realloc(list->items,capacity*sizeof(*grown));
        if(!grown)return 0;list->items=grown;list->capacity=capacity;
    }
    list->items[list->count++]=value;return 1;
}

static int target_append(struct target_list *list,sqlite3_int64 id,const char *mac) {
    struct export_target *grown,*item;size_t capacity;
    if(list->count==list->capacity) {
        capacity=list->capacity?list->capacity*2:256;
        grown=realloc(list->items,capacity*sizeof(*grown));
        if(!grown)return 0;list->items=grown;list->capacity=capacity;
    }
    item=&list->items[list->count];
    if(!parse_mac(mac,item->mac,item->mac_text))return -1;
    item->id=id;list->count++;return 1;
}

static int write_bytes(const void *data,size_t size) { return fwrite(data,1,size,stdout)==size; }
static int write_u16(uint16_t value) {
    unsigned char bytes[2]={(unsigned char)value,(unsigned char)(value>>8)};
    return write_bytes(bytes,sizeof(bytes));
}
static int write_u32(uint32_t value) {
    unsigned char bytes[4]={(unsigned char)value,(unsigned char)(value>>8),
        (unsigned char)(value>>16),(unsigned char)(value>>24)};
    return write_bytes(bytes,sizeof(bytes));
}

static void make_codebook(uint16_t values[CODE_COUNT]) {
    unsigned i;double ratio=pow(2000.0/101.0,1.0/153.0);
    for(i=0;i<100;i++)values[i]=(uint16_t)(i+1);
    for(i=0;i<154;i++)values[100+i]=(uint16_t)lround(101.0*pow(ratio,(double)i));
    values[100]=101;values[253]=2000;
}

static unsigned encode_latency(int milliseconds,const uint16_t codebook[CODE_COUNT]) {
    unsigned low=0,high=CODE_COUNT;
    if(milliseconds<1)milliseconds=1;
    if(milliseconds>=2000)return CODE_COUNT;
    while(low<high) { unsigned middle=low+(high-low)/2;
        if(codebook[middle]<(unsigned)milliseconds)low=middle+1;else high=middle; }
    if(low==0)return 1;
    if(low==CODE_COUNT)return CODE_COUNT;
    return milliseconds-codebook[low-1]<=codebook[low]-milliseconds?low:low+1;
}

static int find_round(const struct round_list *rounds,uint32_t timestamp,size_t *position) {
    size_t low=0,high=rounds->count;
    while(low<high) { size_t middle=low+(high-low)/2;
        if(rounds->items[middle]<timestamp)low=middle+1;else high=middle; }
    if(low>=rounds->count||rounds->items[low]!=timestamp)return 0;
    *position=low;return 1;
}

static int load_rounds(sqlite3 *db,uint32_t start,uint32_t end,struct round_list *rounds) {
    sqlite3_stmt *query=NULL;int rc=sqlite3_prepare_v2(db,
        "SELECT DISTINCT observed_at FROM ping_result WHERE observed_at>=? AND observed_at<? ORDER BY observed_at",
        -1,&query,NULL);
    if(rc==SQLITE_OK)sqlite3_bind_int64(query,1,start);
    if(rc==SQLITE_OK)sqlite3_bind_int64(query,2,end);
    if(rc==SQLITE_OK)while((rc=sqlite3_step(query))==SQLITE_ROW)
        if(!round_append(rounds,(uint32_t)sqlite3_column_int64(query,0))){rc=SQLITE_NOMEM;break;}
    sqlite3_finalize(query);return rc==SQLITE_DONE?SQLITE_OK:rc;
}

static int load_targets(sqlite3 *db,uint32_t start,uint32_t end,struct target_list *targets) {
    sqlite3_stmt *query=NULL;int append,rc=sqlite3_prepare_v2(db,
        "SELECT DISTINCT t.id,CASE WHEN t.kind='client' THEN t.client_mac ELSE t.ap_id END AS mac "
        "FROM target t JOIN ping_result p ON p.target_id=t.id "
        "WHERE p.observed_at>=? AND p.observed_at<? ORDER BY mac COLLATE NOCASE",
        -1,&query,NULL);
    if(rc==SQLITE_OK)sqlite3_bind_int64(query,1,start);
    if(rc==SQLITE_OK)sqlite3_bind_int64(query,2,end);
    if(rc==SQLITE_OK)while((rc=sqlite3_step(query))==SQLITE_ROW) {
        append=target_append(targets,sqlite3_column_int64(query,0),(const char *)sqlite3_column_text(query,1));
        /* Development builds stored the AP's numeric vendor id instead of its
         * MAC. Ignore those legacy rows; current discovery creates the proper
         * MAC-primary target and one stale row must not hide every device. */
        if(append<0)continue;
        if(!append){rc=SQLITE_NOMEM;break;}
    }
    sqlite3_finalize(query);return rc==SQLITE_DONE?SQLITE_OK:rc;
}

static int export_day(uint32_t start) {
    sqlite3 *db=NULL;sqlite3_stmt *query=NULL;struct round_list rounds={0};struct target_list targets={0};
    uint16_t codebook[CODE_COUNT];unsigned char *row=NULL,zero[64]={0};uint32_t end=start+86400U;
    uint32_t timestamps_offset=HEADER_SIZE,macs_offset,samples_offset;size_t i;int rc=sqlite3_open_v2(DB,&db,SQLITE_OPEN_READONLY,NULL);
    if(rc==SQLITE_OK)rc=load_rounds(db,start,end,&rounds);
    if(rc==SQLITE_OK)rc=load_targets(db,start,end,&targets);
    if(rounds.count>UINT32_MAX||targets.count>UINT32_MAX)rc=SQLITE_TOOBIG;
    macs_offset=timestamps_offset+(uint32_t)rounds.count*4U;
    samples_offset=(macs_offset+(uint32_t)targets.count*6U+3U)&~3U;make_codebook(codebook);
    if(rc==SQLITE_OK&&(!write_bytes("ZDPMDAY\0",8)||!write_u16(FORMAT_VERSION)||!write_u16(HEADER_SIZE)
        ||!write_u32(1)||!write_u32(start)||!write_u32(end)||!write_u32((uint32_t)rounds.count)
        ||!write_u32((uint32_t)targets.count)||!write_u16(TIMEOUT_MS)||!write_u16(CODE_COUNT)
        ||!write_u32(timestamps_offset)||!write_u32(macs_offset)||!write_u32(samples_offset)
        ||!write_u32(rounds.count?rounds.items[rounds.count-1]:start)||!write_bytes(zero,12)))rc=SQLITE_IOERR_WRITE;
    for(i=0;rc==SQLITE_OK&&i<CODE_COUNT;i++)if(!write_u16(codebook[i]))rc=SQLITE_IOERR_WRITE;
    if(rc==SQLITE_OK&&!write_bytes(zero,4))rc=SQLITE_IOERR_WRITE;
    for(i=0;rc==SQLITE_OK&&i<rounds.count;i++)if(!write_u32(rounds.items[i]))rc=SQLITE_IOERR_WRITE;
    for(i=0;rc==SQLITE_OK&&i<targets.count;i++)if(!write_bytes(targets.items[i].mac,6))rc=SQLITE_IOERR_WRITE;
    for(i=macs_offset+(uint32_t)targets.count*6U;rc==SQLITE_OK&&i<samples_offset;i++)if(putchar(0)==EOF)rc=SQLITE_IOERR_WRITE;
    row=calloc(rounds.count?rounds.count:1,1);if(!row&&rc==SQLITE_OK)rc=SQLITE_NOMEM;
    if(rc==SQLITE_OK)rc=sqlite3_prepare_v2(db,"SELECT observed_at,rtt_ms,responded,state FROM ping_result WHERE target_id=? AND observed_at>=? AND observed_at<? ORDER BY observed_at,id",-1,&query,NULL);
    for(i=0;rc==SQLITE_OK&&i<targets.count;i++) {
        memset(row,0,rounds.count);sqlite3_bind_int64(query,1,targets.items[i].id);sqlite3_bind_int64(query,2,start);sqlite3_bind_int64(query,3,end);
        while((rc=sqlite3_step(query))==SQLITE_ROW) {
            size_t position;uint32_t timestamp=(uint32_t)sqlite3_column_int64(query,0);const char *state=(const char *)sqlite3_column_text(query,3);
            if(!find_round(&rounds,timestamp,&position))continue;
            if(sqlite3_column_int(query,2)&&state&&!strcmp(state,"ok"))row[position]=(unsigned char)encode_latency(sqlite3_column_int(query,1),codebook);
            else if(state&&!strcmp(state,"timeout"))row[position]=255;else row[position]=0;
        }
        if(rc==SQLITE_DONE)rc=SQLITE_OK;
        if(rc==SQLITE_OK&&!write_bytes(row,rounds.count))rc=SQLITE_IOERR_WRITE;
        sqlite3_reset(query);sqlite3_clear_bindings(query);
    }
    sqlite3_finalize(query);free(row);free(rounds.items);free(targets.items);sqlite3_close(db);
    if(rc!=SQLITE_OK)fprintf(stderr,"daily export failed (rc=%d): %s\n",rc,sqlite3_errstr(rc));return rc==SQLITE_OK?0:1;
}

static int targets_json(void) {
    sqlite3 *db=NULL;sqlite3_stmt *query=NULL;int first=1,rc=sqlite3_open_v2(DB,&db,SQLITE_OPEN_READONLY,NULL);
    if(rc!=SQLITE_OK){puts("{\"status\":\"waiting\",\"targets\":[]}");sqlite3_close(db);return 0;}
    rc=sqlite3_prepare_v2(db,"SELECT kind,ip,client_mac,ap_id,name,enabled FROM target ORDER BY lower(CASE WHEN kind='client' THEN client_mac ELSE ap_id END)",-1,&query,NULL);
    if(rc!=SQLITE_OK){sqlite3_close(db);return 1;}
    printf("{\"status\":\"ok\",\"format_version\":%u,\"generated_at\":%lld,\"targets\":[",FORMAT_VERSION,(long long)time(NULL));
    while((rc=sqlite3_step(query))==SQLITE_ROW) {
        unsigned char mac[6];char canonical[18];const char *kind=(const char *)sqlite3_column_text(query,0);
        const char *identity=(const char *)sqlite3_column_text(query,!kind||strcmp(kind,"client")?3:2);
        if(!parse_mac(identity,mac,canonical))continue;
        if(!first)putchar(',');first=0;fputs("{\"mac\":",stdout);json(canonical);fputs(",\"kind\":",stdout);json(kind);
        fputs(",\"ip\":",stdout);json((const char *)sqlite3_column_text(query,1));fputs(",\"ap_id\":",stdout);json((const char *)sqlite3_column_text(query,3));
        fputs(",\"name\":",stdout);json((const char *)sqlite3_column_text(query,4));printf(",\"enabled\":%s}",sqlite3_column_int(query,5)?"true":"false");
    }
    puts("]}");sqlite3_finalize(query);sqlite3_close(db);return rc==SQLITE_DONE?0:1;
}

static int daily_manifest(void) {
    DIR *directory=opendir(DAILY_DIR);struct dirent *entry;int first=1;char path[512];struct stat info;
    struct period_file periods[64];size_t count=0,i,j;
    printf("{\"status\":\"ok\",\"format_version\":1,\"generated_at\":%lld,\"periods\":[",(long long)time(NULL));
    if(directory)while(count<64&&(entry=readdir(directory))!=NULL) {
        unsigned long start;char tail;const char *name=entry->d_name;
        if(sscanf(name,"ping-%lu.bin.gz%c",&start,&tail)!=1||start>UINT32_MAX)continue;
        snprintf(path,sizeof(path),"%s/%s",DAILY_DIR,name);if(stat(path,&info)!=0)continue;
        periods[count].start=start;periods[count].bytes=(long long)info.st_size;
        periods[count].revision=(long long)info.st_mtime;
        snprintf(periods[count].name,sizeof(periods[count].name),"%s",name);count++;
    }
    if(directory)closedir(directory);
    for(i=0;i<count;i++)for(j=i+1;j<count;j++)if(periods[j].start<periods[i].start){struct period_file swap=periods[i];periods[i]=periods[j];periods[j]=swap;}
    for(i=0;i<count;i++){if(!first)putchar(',');first=0;printf("{\"start\":%lu,\"end\":%lu,\"file\":",periods[i].start,periods[i].start+86400UL);json(periods[i].name);printf(",\"bytes\":%lld,\"revision\":%lld,\"immutable\":true}",periods[i].bytes,periods[i].revision);}
    snprintf(path,sizeof(path),"%s/ping-current.bin.gz",DAILY_DIR);
    if(stat(path,&info)==0) {
        FILE *marker;unsigned long start=0;snprintf(path,sizeof(path),"%s/current-day",DAILY_DIR);marker=fopen(path,"r");
        if(marker){if(fscanf(marker,"%lu",&start)!=1)start=0;fclose(marker);}
        if(start&&start<=UINT32_MAX){if(!first)putchar(',');printf("{\"start\":%lu,\"end\":%lu,\"file\":\"ping-current.bin.gz\",\"bytes\":%lld,\"immutable\":false}",start,start+86400UL,(long long)info.st_size);}
    }
    puts("]}");return 0;
}

int main(int argc,char **argv) {
    char *end=NULL;unsigned long start;
    if(argc==2&&!strcmp(argv[1],"targets-json"))return targets_json();
    if(argc==2&&!strcmp(argv[1],"manifest"))return daily_manifest();
    if(argc==3&&!strcmp(argv[1],"export-day")) {
        start=strtoul(argv[2],&end,10);
        if(!argv[2][0]||*end||start>UINT32_MAX||start%86400UL){fprintf(stderr,"export-day requires a UTC day epoch\n");return 2;}
        return export_day((uint32_t)start);
    }
    fprintf(stderr,"Usage: %s {targets-json|manifest|export-day UTC_DAY_EPOCH}\n",argv[0]);return 2;
}
