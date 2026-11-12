#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

typedef ssize_t (*write_fn)(int, const void *, size_t);

static void fd_path(int fd, char *target, size_t size) {
  char link_path[64];
  snprintf(link_path, sizeof(link_path), "/proc/self/fd/%d", fd);
  ssize_t length = readlink(link_path, target, size - 1);
  if (length < 0) target[0] = '\0';
  else target[length] = '\0';
}

/* Raw syscalls keep the test tracer out of its own interposition hooks. */
static void trace(const char *op, const char *path) {
  const char *output = getenv("MOONDELTA_TRACE");
  if (output == NULL) return;
  int fd = (int)syscall(SYS_openat, AT_FDCWD, output,
                        O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0600);
  if (fd < 0) return;
  char line[8192];
  int length = snprintf(line, sizeof(line), "%s %s\n", op, path);
  if (length > 0 && (size_t)length < sizeof(line)) syscall(SYS_write, fd, line, length);
  syscall(SYS_close, fd);
}

static int fail(const char *op, const char *path) {
  static int hits;
  const char *wanted = getenv("MOONDELTA_FAIL_OP");
  const char *match = getenv("MOONDELTA_FAIL_MATCH");
  if (wanted == NULL || match == NULL || strcmp(wanted, op) != 0 || strstr(path, match) == NULL) return 0;
  if (getenv("MOONDELTA_FAIL_EXACT") != NULL && strcmp(path, match) != 0) return 0;
  const char *nth = getenv("MOONDELTA_FAIL_NTH");
  if (++hits != (nth == NULL ? 1 : atoi(nth))) return 0;
  const char *code = getenv("MOONDELTA_FAIL_ERRNO");
  char event[64];
  snprintf(event, sizeof(event), "fail-%s", op);
  trace(event, path);
  errno = code == NULL ? EIO : atoi(code);
  return 1;
}

int fsync(int fd) {
  static int (*real_fsync)(int);
  if (real_fsync == NULL) real_fsync = dlsym(RTLD_NEXT, "fsync");
  char path[4096];
  fd_path(fd, path, sizeof(path));
  trace("fsync", path);
  if (fail("fsync", path)) return -1;
  return real_fsync(fd);
}

int link(const char *source, const char *destination) {
  static int (*real_link)(const char *, const char *);
  if (real_link == NULL) real_link = dlsym(RTLD_NEXT, "link");
  trace("link", destination);
  if (fail("link", destination)) return -1;
  const char *match = getenv("MOONDELTA_LINK_MATCH");
  const char *ready = getenv("MOONDELTA_LINK_READY");
  const char *release = getenv("MOONDELTA_LINK_RELEASE");
  if (match != NULL && ready != NULL && release != NULL && strstr(destination, match) != NULL) {
    int marker = (int)syscall(SYS_openat, AT_FDCWD, ready,
                              O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
    if (marker >= 0) syscall(SYS_close, marker);
    while (access(release, F_OK) != 0) usleep(1000);
  }
  return real_link(source, destination);
}

int unlink(const char *path) {
  static int (*real_unlink)(const char *);
  if (real_unlink == NULL) real_unlink = dlsym(RTLD_NEXT, "unlink");
  trace("unlink", path);
  if (fail("unlink", path)) return -1;
  return real_unlink(path);
}

ssize_t write(int fd, const void *buffer, size_t count) {
  static write_fn real_write;
  if (real_write == NULL) real_write = (write_fn)dlsym(RTLD_NEXT, "write");
  char path[4096];
  fd_path(fd, path, sizeof(path));
  trace("write", path);
  if (fail("write", path)) return -1;
  const char *match = getenv("MOONDELTA_BLOCK_MATCH");
  const char *ready = getenv("MOONDELTA_BLOCK_READY");
  if (match != NULL && ready != NULL) {
    char link_path[64], target[4096];
    snprintf(link_path, sizeof(link_path), "/proc/self/fd/%d", fd);
    ssize_t length = readlink(link_path, target, sizeof(target) - 1);
    if (length > 0) {
      target[length] = '\0';
      if (strstr(target, match) != NULL) {
        size_t prefix = count < 4096 ? count / 2 : 4096;
        if (prefix == 0) prefix = count;
        ssize_t written = real_write(fd, buffer, prefix);
        if (written <= 0) return written;
        int marker = (int)syscall(SYS_openat, AT_FDCWD, ready,
                                  O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
        if (marker >= 0) syscall(SYS_close, marker);
        for (;;) syscall(SYS_sched_yield);
      }
    }
  }
  return real_write(fd, buffer, count);
}
