#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

#define SOCKET_PATH "/tmp/getstate.socket"
#define RESPONSE_PATH "/tmp/getstat_response"

static const char *selector_for(const char *kind)
{
    if (strcmp(kind, "ap") == 0)
        return "<ajax-request><ap LEVEL=\"1\" caller=\"ap-summary\"/></ajax-request>";
    if (strcmp(kind, "client") == 0)
        return "<ajax-request><client LEVEL=\"1\"/><pieceStat start=\"0\" number=\"300\" pid=\"1\" requestId=\"zd1200.snapshot\"/></ajax-request>";
    if (strcmp(kind, "mesh") == 0)
        return "<ajax-request><meshview/></ajax-request>";
    return NULL;
}

static int copy_response(const char *destination)
{
    static const char prefix[] = "<?xml version=\"1.0\" encoding=\"utf-8\"?><!DOCTYPE ajax-response><ajax-response><response type=\"object\" id=\"zd1200.snapshot\">";
    static const char suffix[] = "</response></ajax-response>";
    char buffer[16384];
    ssize_t length;
    int input = -1, output = -1, result = -1;
    struct stat status;

    input = open(RESPONSE_PATH, O_RDONLY | O_NOFOLLOW);
    if (input < 0 || fstat(input, &status) < 0 || !S_ISREG(status.st_mode))
        goto done;
    output = open(destination, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
    if (output < 0)
        goto done;
    if (write(output, prefix, sizeof prefix - 1) != (ssize_t)(sizeof prefix - 1))
        goto done;
    while ((length = read(input, buffer, sizeof buffer)) > 0) {
        char *at = buffer;
        while (length > 0) {
            ssize_t written = write(output, at, (size_t)length);
            if (written <= 0)
                goto done;
            at += written;
            length -= written;
        }
    }
    if (length == 0 &&
        write(output, suffix, sizeof suffix - 1) == (ssize_t)(sizeof suffix - 1) &&
        fsync(output) == 0)
        result = 0;
done:
    if (input >= 0) close(input);
    if (output >= 0) close(output);
    if (result != 0) unlink(destination);
    return result;
}

int main(int argc, char **argv)
{
    struct sockaddr_un address;
    const char *selector;
    char request_path[] = "/tmp/zd1200-getstat-request.XXXXXX";
    char reply[256];
    struct timeval timeout = { 20, 0 };
    ssize_t length;
    int request, sock;

    if (argc != 3 || !(selector = selector_for(argv[1]))) {
        fprintf(stderr, "usage: %s ap|client|mesh OUTPUT\n", argv[0]);
        return 2;
    }
    if (unlink(RESPONSE_PATH) < 0 && errno != ENOENT) {
        perror("unlink response");
        return 1;
    }
    request = mkstemp(request_path);
    if (request < 0 || fchmod(request, 0600) < 0 ||
        write(request, selector, strlen(selector)) != (ssize_t)strlen(selector) ||
        fsync(request) < 0 || close(request) < 0) {
        perror("write request");
        if (request >= 0) close(request);
        unlink(request_path);
        return 1;
    }
    sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) {
        perror("socket");
        return 1;
    }
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
    memset(&address, 0, sizeof address);
    address.sun_family = AF_UNIX;
    strncpy(address.sun_path, SOCKET_PATH, sizeof address.sun_path - 1);
    if (connect(sock, (struct sockaddr *)&address, sizeof address) < 0 ||
        write(sock, request_path, strlen(request_path)) != (ssize_t)strlen(request_path)) {
        perror("getstat request");
        close(sock);
        unlink(request_path);
        return 1;
    }
    shutdown(sock, SHUT_WR);
    length = read(sock, reply, sizeof reply - 1);
    close(sock);
    unlink(request_path);
    if (length <= 0) {
        fprintf(stderr, "getstatd returned no reply\n");
        return 1;
    }
    reply[length] = '\0';
    if (!strstr(reply, "stat_file=/tmp/getstat_response") ||
        copy_response(argv[2]) != 0) {
        fprintf(stderr, "getstatd response unavailable: %s\n", reply);
        return 1;
    }
    return 0;
}
