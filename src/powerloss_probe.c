/* S5 power-loss boundary probe.
 *
 * The probe does NOT simulate a power loss. It only makes the injection point
 * deterministic: when the process reaches the boundary named by
 * MOONDELTA_POWERLOSS_AT it announces it on a serial line and then blocks, so
 * the host can kill QEMU and the volatile NBD service at that exact point
 * instead of racing the guest.
 *
 * Killing this process is a process crash, not a power loss; only the host
 * cutting the device cache produces S5 evidence.
 *
 * Environment (read once, on the first boundary call):
 *   MOONDELTA_POWERLOSS_AT     boundary name, "all"/"*" for every boundary,
 *                              unset/"off" disables the probe entirely
 *   MOONDELTA_POWERLOSS_SIGNAL path announced to, default /dev/ttyS1
 *   MOONDELTA_POWERLOSS_PAUSE  "0" announces and continues, default blocks
 *
 * On Windows this compiles to a no-op: the project claims process-crash
 * consistency only there, and no NTFS power-loss evidence is produced.
 */

#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif

#include <stddef.h>
#include <string.h>

#if !defined(_WIN32)

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define MOONDELTA_PROBE_NAME_MAX 96

static char moondelta_probe_target[MOONDELTA_PROBE_NAME_MAX];
static int moondelta_probe_state; /* 0 = not initialised, 1 = armed, 2 = off */
static int moondelta_probe_pause = 1;
static int moondelta_probe_fd = -1;

static void moondelta_probe_init(void) {
  const char *target = getenv("MOONDELTA_POWERLOSS_AT");
  moondelta_probe_state = 2;
  if (target != NULL && target[0] != '\0' && strcmp(target, "off") != 0) {
    size_t length = strlen(target);
    if (length >= sizeof(moondelta_probe_target)) {
      length = sizeof(moondelta_probe_target) - 1;
    }
    memcpy(moondelta_probe_target, target, length);
    moondelta_probe_target[length] = '\0';
    moondelta_probe_state = 1;
  }
  const char *pause = getenv("MOONDELTA_POWERLOSS_PAUSE");
  if (pause != NULL && strcmp(pause, "0") == 0) {
    moondelta_probe_pause = 0;
  }
  if (moondelta_probe_state == 1) {
    const char *signal_path = getenv("MOONDELTA_POWERLOSS_SIGNAL");
    if (signal_path == NULL || signal_path[0] == '\0') {
      signal_path = "/dev/ttyS1";
    }
    moondelta_probe_fd = open(signal_path, O_WRONLY | O_NOCTTY | O_CLOEXEC);
  }
}

/* Announce the boundary and wait for the UART to shift the line out. A tty is
 * not fsync-able; tcdrain() is the closest equivalent and is best effort. */
static void moondelta_probe_announce(const char *name, const char *detail) {
  char line[320];
  int length = snprintf(line, sizeof(line), "BOUNDARY name=%s detail=%s pid=%ld\n",
                        name, detail == NULL ? "-" : detail, (long)getpid());
  if (moondelta_probe_fd < 0 || length <= 0) return;
  ssize_t offset = 0;
  while (offset < (ssize_t)length) {
    ssize_t written =
        write(moondelta_probe_fd, line + offset, (size_t)(length - offset));
    if (written <= 0) break;
    offset += written;
  }
  (void)tcdrain(moondelta_probe_fd);
}

/* Block until the host cuts power. Returning would turn a deterministic
 * injection point back into a race. */
static void moondelta_probe_block(void) {
  for (;;) {
    struct timespec interval;
    interval.tv_sec = 1;
    interval.tv_nsec = 0;
    (void)nanosleep(&interval, NULL);
  }
}

int moondelta_powerloss_boundary(const char *name, const char *detail) {
  if (name == NULL) return 0;
  if (moondelta_probe_state == 0) moondelta_probe_init();
  if (moondelta_probe_state != 1) return 0;
  int hit = strcmp(moondelta_probe_target, name) == 0 ||
            strcmp(moondelta_probe_target, "all") == 0 ||
            strcmp(moondelta_probe_target, "*") == 0;
  if (!hit) return 0;
  moondelta_probe_announce(name, detail);
  if (moondelta_probe_pause) moondelta_probe_block();
  return 1;
}

#else /* _WIN32 */

int moondelta_powerloss_boundary(const char *name, const char *detail) {
  (void)name;
  (void)detail;
  return 0;
}

#endif
