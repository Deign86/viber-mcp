// Viber MCP.app launcher (macOS).
//
// Claude starts MCP servers with responsibility disclaimed, so macOS asks for — and records —
// Accessibility and Screen Recording for whatever binary comes first: `uv`, which would then let
// every script run through uv control the Mac. Started through this app bundle instead, the
// server runs as our child and inherits "Viber MCP" as the responsible app, so the grant covers
// this server only.
//
// The command to run is Contents/Resources/command, one argument per line (written by
// setup_macos.sh). stdin/stdout/stderr are inherited (the MCP stdio transport passes straight
// through); SIGTERM/SIGINT/SIGHUP are forwarded; the exit status is the server's.

#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(void) {
    char exe[PATH_MAX], real[PATH_MAX], conf[PATH_MAX + 32];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) {
        fprintf(stderr, "viber-mcp launcher: can't find its own path\n");
        return 1;
    }
    char *slash = strrchr(real, '/');  // .../Contents/MacOS/viber-mcp -> .../Contents
    if (slash) *slash = '\0';
    slash = strrchr(real, '/');
    if (slash) *slash = '\0';
    snprintf(conf, sizeof conf, "%s/Resources/command", real);

    FILE *f = fopen(conf, "r");
    if (!f) {
        fprintf(stderr, "viber-mcp launcher: %s is missing (run setup_macos.sh)\n", conf);
        return 1;
    }
    char *args[64];
    int n = 0;
    char line[PATH_MAX];
    while (n < 63 && fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = '\0';
        if (line[0]) args[n++] = strdup(line);
    }
    fclose(f);
    args[n] = NULL;
    if (n == 0) {
        fprintf(stderr, "viber-mcp launcher: %s is empty\n", conf);
        return 1;
    }

    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);
    int err = posix_spawn(&child, args[0], NULL, NULL, args, environ);
    if (err != 0) {
        fprintf(stderr, "viber-mcp launcher: can't start %s: %s\n", args[0], strerror(err));
        return 1;
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) return 1;  // EINTR: a forwarded signal; keep waiting for the server
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}
