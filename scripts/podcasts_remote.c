// podcasts-remote: move Apple Podcasts on this Mac to a position in an episode, silently.
//
// Apple Podcasts uploads its play position to iCloud as soon as it changes, and the
// iPhone picks it up from there. This tool changes that position the same way the
// Podcasts notification extension and Control Center do, through the MediaRemote
// framework, aimed at com.apple.podcasts only:
//   1. hand Podcasts a one-episode playback queue, with "do not start playing",
//   2. seek that (paused) episode to the time.
// Nothing plays. MediaRemote is a private framework: the Python helper treats every
// failure here as "not pushed" and keeps the Shortcut link as the fallback.
//
//   podcasts-remote playing                          prints "playing" or "idle"
//   podcasts-remote push <collection> <track> <sec>  prints "sent", "busy" or an error
//
// Exit codes: 0 sent / idle, 3 Podcasts is playing (nothing done), 1 usage,
// 2 MediaRemote missing, 4 queue refused, 5 seek refused.
// Built by scripts/install.sh. Measured on macOS 26.6 (Podcasts 4025.700).
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PODCASTS "com.apple.podcasts"
enum { CMD_PAUSE = 1, CMD_SEEK = 24 };            // MRMediaRemoteCommand values
enum { QUEUE_GENERIC_IDS = 5, SHUFFLE_OFF = 1, REPLACE_QUEUE = 2 };  // as the Podcasts extension uses them

static void *mr;

// ---- is Apple Podcasts making sound right now? (public CoreAudio, macOS 14.2+) ----
static int podcasts_playing(void) {
    AudioObjectPropertyAddress list = {kAudioHardwarePropertyProcessObjectList, kAudioObjectPropertyScopeGlobal,
                                       kAudioObjectPropertyElementMain};
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &list, 0, NULL, &size) || size == 0) return 0;
    AudioObjectID *ids = malloc(size);
    int playing = 0;
    if (ids && !AudioObjectGetPropertyData(kAudioObjectSystemObject, &list, 0, NULL, &size, ids)) {
        for (UInt32 i = 0; i < size / sizeof(AudioObjectID) && !playing; i++) {
            AudioObjectPropertyAddress bid = {kAudioProcessPropertyBundleID, kAudioObjectPropertyScopeGlobal,
                                              kAudioObjectPropertyElementMain};
            AudioObjectPropertyAddress out = {kAudioProcessPropertyIsRunningOutput, kAudioObjectPropertyScopeGlobal,
                                              kAudioObjectPropertyElementMain};
            CFStringRef bundle = NULL;
            UInt32 running = 0, s1 = sizeof bundle, s2 = sizeof running;
            if (AudioObjectGetPropertyData(ids[i], &bid, 0, NULL, &s1, &bundle) || !bundle) continue;
            if (CFStringCompare(bundle, CFSTR(PODCASTS), 0) == kCFCompareEqualTo &&
                !AudioObjectGetPropertyData(ids[i], &out, 0, NULL, &s2, &running) && running)
                playing = 1;
            CFRelease(bundle);
        }
    }
    free(ids);
    return playing;
}

// ---- MediaRemote ------------------------------------------------------------
typedef void (*SendToAppFn)(int, CFDictionaryRef, void *, CFStringRef, int, dispatch_queue_t, void (^)(int, CFArrayRef));
typedef void *(*QueueCreateFn)(CFAllocatorRef, int);
typedef void (*QueueSetArrayFn)(void *, CFArrayRef);
typedef void (*QueueSetIntFn)(void *, long);
typedef void *(*OriginFn)(void);
typedef void *(*ClientCreateFn)(int, CFStringRef);
typedef void *(*PlayerCreateFn)(CFStringRef, CFStringRef);
typedef void *(*PathCreateFn)(void *, void *, void *);
typedef void (*SetQueueFn)(void *, CFDictionaryRef, void *, void *, dispatch_queue_t, void (^)(unsigned int, void *));

static void *need(const char *name) {
    void *p = dlsym(mr, name);
    if (!p) {
        printf("missing %s\n", name);
        exit(2);
    }
    return p;
}

// Send one command to Podcasts (never to whatever app is "now playing"). Returns the
// handler status: 0 success, 1 no such content, 2 failed, 3 nothing to act on; -1 timeout.
static long send_command(int command, double position) {
    SendToAppFn send = (SendToAppFn)need("MRMediaRemoteSendCommandToApp");
    CFMutableDictionaryRef opts = NULL;
    if (command == CMD_SEEK) {
        CFStringRef *key = (CFStringRef *)need("kMRMediaRemoteOptionPlaybackPosition");
        CFNumberRef n = CFNumberCreate(NULL, kCFNumberDoubleType, &position);
        opts = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
        CFDictionarySetValue(opts, *key, n);
        CFRelease(n);
    }
    __block long status = -1;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    send(command, opts, NULL, CFSTR(PODCASTS), 0, dispatch_get_global_queue(0, 0), ^(int err, CFArrayRef statuses) {
        status = err;
        if (!err && statuses && CFArrayGetCount(statuses) > 0)
            CFNumberGetValue(CFArrayGetValueAtIndex(statuses, 0), kCFNumberLongType, &status);
        dispatch_semaphore_signal(done);
    });
    dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));
    if (opts) CFRelease(opts);
    return status;
}

// Replace the Podcasts queue with one episode, paused. Returns 0 when Podcasts accepted it.
static long load_episode(long long collection, long long track) {
    char ident[160];
    snprintf(ident, sizeof ident, "podcasts://playItem?storeTrackId=%lld&storeCollectionId=%lld", track, collection);
    CFStringRef s = CFStringCreateWithCString(NULL, ident, kCFStringEncodingUTF8);
    CFArrayRef ids = CFArrayCreate(NULL, (const void **)&s, 1, &kCFTypeArrayCallBacks);
    void *queue = ((QueueCreateFn)need("MRSystemAppPlaybackQueueCreate"))(kCFAllocatorDefault, QUEUE_GENERIC_IDS);
    ((QueueSetArrayFn)need("MRSystemAppPlaybackQueueSetGenericTrackIdentifiers"))(queue, ids);
    ((QueueSetIntFn)need("MRSystemAppPlaybackQueueSetTracklistShuffleMode"))(queue, SHUFFLE_OFF);
    ((QueueSetIntFn)need("MRSystemAppPlaybackQueueSetReplaceIntent"))(queue, REPLACE_QUEUE);
    ((QueueSetIntFn)need("MRSystemAppPlaybackQueueSetIsRequestingImmediatePlayback"))(queue, 0);
    void *path = ((PathCreateFn)need("MRNowPlayingPlayerPathCreate"))(
        ((OriginFn)need("MRMediaRemoteGetLocalOrigin"))(),
        ((ClientCreateFn)need("MRNowPlayingClientCreate"))(0, CFSTR(PODCASTS)),
        ((PlayerCreateFn)need("MRNowPlayingPlayerCreate"))(NULL, NULL));
    CFStringRef *key = (CFStringRef *)need("kMRMediaRemoteOptionAssistantSetQueueTrueCompletion");
    const void *k[] = {*key}, *v[] = {kCFBooleanTrue};
    CFDictionaryRef opts = CFDictionaryCreate(NULL, k, v, 1, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    __block long status = -1;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    ((SetQueueFn)need("MRMediaRemoteSetAppPlaybackQueueForPlayer"))(
        queue, opts, path, NULL, dispatch_get_global_queue(0, 0), ^(unsigned int st, void *unused) {
            (void)unused;
            status = st;
            dispatch_semaphore_signal(done);
        });
    dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC));
    return status;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "playing")) {
        puts(podcasts_playing() ? "playing" : "idle");
        return 0;
    }
    if (argc != 5 || strcmp(argv[1], "push")) {
        fprintf(stderr, "usage: %s playing | push <collection-id> <track-id> <seconds>\n", argv[0]);
        return 1;
    }
    long long collection = atoll(argv[2]), track = atoll(argv[3]);
    double seconds = atof(argv[4]);
    if (collection <= 0 || track <= 0 || seconds < 0) return 1;
    mr = dlopen("/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote", RTLD_NOW);
    if (!mr) {
        puts("no MediaRemote");
        return 2;
    }
    if (podcasts_playing()) {  // you are listening on this Mac: leave it alone
        puts("busy");
        return 3;
    }
    long st = load_episode(collection, track);
    if (st != 0) {
        printf("queue refused (%ld)\n", st);
        return 4;
    }
    // The episode loads in the background; a seek before it is ready has nothing to act on.
    for (int attempt = 0; attempt < 40; attempt++) {
        usleep(250000);
        if (podcasts_playing()) send_command(CMD_PAUSE, 0);  // only ours can be playing now
        st = send_command(CMD_SEEK, seconds);
        if (st == 0) {
            if (podcasts_playing()) send_command(CMD_PAUSE, 0);
            puts("sent");
            return 0;
        }
    }
    printf("seek refused (%ld)\n", st);
    return 5;
}
