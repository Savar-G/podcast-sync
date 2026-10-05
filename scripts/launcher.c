// Tiny app-bundle launcher for the Python helper.
//
// macOS privacy prompts ("... would like to access data from other apps") are
// attached to the *responsible* app. Running Python through this bundle makes
// "Podcast Sync Helper" the app you allow, once, instead of a Python binary whose
// identity changes with every Python install or upgrade.
//
// Built by scripts/install.sh with -DPYTHON=... -DHELPER_DIR=...
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(void) {
    char *args[] = {PYTHON, "-m", "podsync", NULL};
    if (chdir(HELPER_DIR) != 0) {
        perror("podcast-sync: chdir " HELPER_DIR);
        return 1;
    }
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    if (posix_spawn(&child, PYTHON, NULL, NULL, args, environ) != 0) {
        perror("podcast-sync: spawn " PYTHON);
        return 1;
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
