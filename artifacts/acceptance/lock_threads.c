/* Same-process, real-thread contention test for the table maintenance lock.
 *
 * The S1-S5 roadmap asks for "one process, several independent lock handles,
 * many threads" evidence for the writer/recoverer mutual exclusion, and the
 * review of 2026-11-12 recorded that only *sequential* multi-handle calls and
 * *multi-process* contention had been shown. Sequential calls cannot prove
 * anything about concurrency, so this program drives the very functions the
 * MoonBit writer calls (`moondelta_lock_table` / `moondelta_unlock_table`, the
 * FFI pair behind `acquire_shared_lock` / `try_acquire_exclusive_lock`) from
 * real threads inside a single process.
 *
 * It is compiled against the project's own C source, so it tests the shipped
 * implementation, not a model of it.
 *
 * Build (POSIX):  cc -O2 -I"$MOON_HOME/include" lock_threads.c -o lock_threads -pthread
 * Build (Windows): gcc -O2 -I"%USERPROFILE%\.moon\include" lock_threads.c -o lock_threads.exe
 *
 * Exit 0 only when every case holds. Nothing here is power-loss evidence.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <direct.h>
#include <process.h>
#include <windows.h>
#define sleep_ms(ms) Sleep(ms)
#else
#include <pthread.h>
#include <sys/stat.h>
#include <unistd.h>
#define sleep_ms(ms) usleep((ms) * 1000)
#endif

/* The real implementation. Its only external dependency is the power-loss probe,
 * which is inactive without MOONDELTA_POWERLOSS_AT; a no-op keeps this test
 * independent of the probe's build. */
int moondelta_powerloss_boundary(const char *name, const char *detail);
int moondelta_powerloss_boundary(const char *name, const char *detail) {
  (void)name;
  (void)detail;
  return 0;
}

#include "atomic_file_native.c"

#define LOCK_BUSY (-11)
#define MAX_THREADS 16

static int failures = 0;

static void check(int condition, const char *ok, const char *bad) {
  if (condition) {
    printf("  ok    %s\n", ok);
  } else {
    printf("  FAIL  %s\n", bad);
    failures++;
  }
}

/* --------------------------------------------------------------------------
 * minimal portable threading
 */

typedef struct {
  void (*fn)(void *);
  void *arg;
#ifdef _WIN32
  HANDLE handle;
#else
  pthread_t thread;
#endif
} worker_t;

static void *worker_trampoline(void *raw) {
  worker_t *worker = (worker_t *)raw;
  worker->fn(worker->arg);
  return NULL;
}

static void worker_start(worker_t *worker, void (*fn)(void *), void *arg) {
  worker->fn = fn;
  worker->arg = arg;
#ifdef _WIN32
  worker->handle = CreateThread(NULL, 0, (LPTHREAD_START_ROUTINE)worker_trampoline,
                                worker, 0, NULL);
  if (worker->handle == NULL) {
    printf("  FAIL  could not start a thread\n");
    failures++;
  }
#else
  if (pthread_create(&worker->thread, NULL, worker_trampoline, worker) != 0) {
    printf("  FAIL  could not start a thread\n");
    failures++;
  }
#endif
}

static void worker_join(worker_t *worker) {
#ifdef _WIN32
  if (worker->handle != NULL) {
    WaitForSingleObject(worker->handle, INFINITE);
    CloseHandle(worker->handle);
    worker->handle = NULL;
  }
#else
  pthread_join(worker->thread, NULL);
#endif
}

/* The lock root as the FFI expects it: a byte string, not a C string. */
static uint8_t *root_bytes(const char *root) {
  return (uint8_t *)strdup(root);
}

/* --------------------------------------------------------------------------
 * case 1: exclusive holders never overlap
 */

typedef struct {
  uint8_t *root;
  int iterations;
  volatile int *inside;
  volatile int *overlaps;
  volatile int *acquired;
  volatile int *released;
} exclusive_case_t;

static void exclusive_worker(void *raw) {
  exclusive_case_t *c = (exclusive_case_t *)raw;
  for (int i = 0; i < c->iterations; i++) {
    int handle = moondelta_lock_table(c->root, 1);
    if (handle <= 0) {
      printf("  FAIL  exclusive acquire failed (error %d)\n", handle);
      __sync_fetch_and_add(c->overlaps, 1);
      return;
    }
    if (__sync_fetch_and_add(c->inside, 1) != 0) {
      __sync_fetch_and_add(c->overlaps, 1);
    }
    __sync_fetch_and_add(c->acquired, 1);
    sleep_ms(1);
    __sync_fetch_and_sub(c->inside, 1);
    if (moondelta_unlock_table(handle) != 0) {
      printf("  FAIL  exclusive release failed\n");
      __sync_fetch_and_add(c->overlaps, 1);
      return;
    }
    __sync_fetch_and_add(c->released, 1);
  }
}

static void case_exclusive_serializes(const char *root) {
  printf("exclusive lock serializes threads of one process\n");
  volatile int inside = 0;
  volatile int overlaps = 0;
  volatile int acquired = 0;
  volatile int released = 0;
  enum { THREADS = 4, ITERATIONS = 40 };
  worker_t workers[THREADS];
  exclusive_case_t case_data = {root_bytes(root), ITERATIONS, &inside, &overlaps,
                               &acquired, &released};
  for (int i = 0; i < THREADS; i++) {
    worker_start(&workers[i], exclusive_worker, &case_data);
  }
  for (int i = 0; i < THREADS; i++) {
    worker_join(&workers[i]);
  }
  printf("        acquired=%d released=%d overlaps=%d\n", acquired, released, overlaps);
  check(overlaps == 0, "no two threads held the exclusive lock at the same time",
        "threads overlapped while holding the exclusive lock");
  check(acquired == THREADS * ITERATIONS && released == acquired,
        "every acquisition was matched by a release",
        "acquisitions and releases do not balance");
  free((void *)case_data.root);
}

/* --------------------------------------------------------------------------
 * case 2/5: a held lock is visible as busy to other threads
 */

typedef struct {
  uint8_t *root;
  int mode;
  volatile int *result;
  volatile int *done;
} attempt_t;

static void attempt_worker(void *raw) {
  attempt_t *attempt = (attempt_t *)raw;
  *attempt->result = moondelta_lock_table(attempt->root, attempt->mode);
  __sync_fetch_and_add(attempt->done, 1);
}

static int attempt_locked(uint8_t *root, int mode) {
  volatile int result = 0;
  volatile int done = 0;
  attempt_t attempt = {root, mode, &result, &done};
  worker_t worker;
  worker_start(&worker, attempt_worker, &attempt);
  worker_join(&worker);
  return result;
}

static void case_busy_is_visible(const char *root) {
  printf("a held lock is busy for another thread of the same process\n");
  uint8_t *bytes = root_bytes(root);
  int writer = moondelta_lock_table(bytes, 1);
  check(writer > 0, "the writer thread holds the exclusive lock", "writer acquire failed");
  int recoverer = attempt_locked(bytes, 2);
  check(recoverer == LOCK_BUSY,
        "the recoverer's non-blocking exclusive attempt reports busy",
        "the recoverer did not see the writer's lock as busy");
  check(moondelta_unlock_table(writer) == 0, "the writer releases the lock",
        "writer release failed");
  int after = attempt_locked(bytes, 2);
  check(after > 0, "after the release the recoverer acquires the lock",
        "the lock stayed busy after the writer released it");
  if (after > 0) moondelta_unlock_table(after);
  free(bytes);
}

/* --------------------------------------------------------------------------
 * case 3: shared holders coexist, and block an exclusive recoverer
 */

typedef struct {
  uint8_t *root;
  volatile int *arrived;
  volatile int *release_now;
  volatile int *handles;
  volatile int *errors;
  int index;
} shared_case_t;

static void shared_worker(void *raw) {
  shared_case_t *c = (shared_case_t *)raw;
  int handle = moondelta_lock_table(c->root, 0);
  if (handle <= 0) {
    __sync_fetch_and_add(c->errors, 1);
    __sync_fetch_and_add(c->arrived, 1);
    return;
  }
  c->handles[c->index] = handle;
  __sync_fetch_and_add(c->arrived, 1);
  while (!*c->release_now) {
    sleep_ms(1);
  }
  moondelta_unlock_table(c->handles[c->index]);
}

static void case_shared_coexists(const char *root) {
  printf("shared holders coexist while an exclusive recoverer is refused\n");
  enum { THREADS = 4 };
  uint8_t *bytes = root_bytes(root);
  volatile int arrived = 0;
  volatile int release_now = 0;
  volatile int errors = 0;
  volatile int handles[THREADS];
  worker_t workers[THREADS];
  shared_case_t cases[THREADS];
  for (int i = 0; i < THREADS; i++) handles[i] = 0;
  for (int i = 0; i < THREADS; i++) {
    cases[i] = (shared_case_t){bytes, &arrived, &release_now, handles, &errors, i};
    worker_start(&workers[i], shared_worker, &cases[i]);
  }
  while (arrived < THREADS) {
    sleep_ms(1);
  }
  check(errors == 0, "every thread acquired the shared lock", "a shared acquire failed");
  check(arrived == THREADS, "all shared holders hold it at the same time",
        "shared holders did not overlap, so reads are being serialized");
  int recoverer = attempt_locked(bytes, 2);
  check(recoverer == LOCK_BUSY,
        "an exclusive attempt is refused while shared holders exist",
        "the recoverer could acquire the exclusive lock while readers held it");
  release_now = 1;
  for (int i = 0; i < THREADS; i++) worker_join(&workers[i]);
  int after = attempt_locked(bytes, 2);
  check(after > 0, "the exclusive lock is available once the readers are gone",
        "the exclusive lock stayed busy after the readers released it");
  if (after > 0) moondelta_unlock_table(after);
  free(bytes);
}

/* --------------------------------------------------------------------------
 * case 4: acquire/release churn must not exhaust or leak lock slots
 */

typedef struct {
  uint8_t *root;
  int iterations;
  volatile int *failures;
} churn_case_t;

static void churn_worker(void *raw) {
  churn_case_t *c = (churn_case_t *)raw;
  for (int i = 0; i < c->iterations; i++) {
    int handle = moondelta_lock_table(c->root, 1);
    if (handle <= 0) {
      __sync_fetch_and_add(c->failures, 1);
      return;
    }
    if (moondelta_unlock_table(handle) != 0) {
      __sync_fetch_and_add(c->failures, 1);
      return;
    }
  }
}

static void case_no_slot_leak(const char *root) {
  printf("repeated threaded acquire/release does not leak lock handles\n");
  enum { THREADS = 8, ITERATIONS = 60 };
  uint8_t *bytes = root_bytes(root);
  volatile int errors = 0;
  worker_t workers[THREADS];
  churn_case_t case_data = {bytes, ITERATIONS, &errors};
  for (int i = 0; i < THREADS; i++) {
    worker_start(&workers[i], churn_worker, &case_data);
  }
  for (int i = 0; i < THREADS; i++) {
    worker_join(&workers[i]);
  }
  check(errors == 0, "every churn acquisition succeeded",
        "an acquisition failed, which means handles or slots leaked");
  int handle = moondelta_lock_table(bytes, 1);
  check(handle > 0, "the lock is still acquirable after the churn",
        "the lock is unusable after repeated threaded use");
  if (handle > 0) moondelta_unlock_table(handle);
  free(bytes);
}

/* --------------------------------------------------------------------------
 * case 5: writer holds shared, recoverer is refused, and after the writer
 * finishes the recoverer wins (the ordering the recovery policy depends on)
 */

static void case_writer_then_recoverer(const char *root) {
  printf("writer (shared) excludes the recoverer, which wins after it finishes\n");
  uint8_t *bytes = root_bytes(root);
  int writer = moondelta_lock_table(bytes, 0);
  check(writer > 0, "the writer holds the shared lock", "writer shared acquire failed");
  int refused = attempt_locked(bytes, 2);
  check(refused == LOCK_BUSY, "the recoverer is refused while a writer is active",
        "the recoverer acquired the exclusive lock beside an active writer");
  check(moondelta_unlock_table(writer) == 0, "the writer finishes and releases",
        "writer release failed");
  int granted = attempt_locked(bytes, 2);
  check(granted > 0, "the recoverer acquires the lock once no writer is active",
        "the recoverer could not acquire the lock after the writer finished");
  if (granted > 0) moondelta_unlock_table(granted);
  free(bytes);
}

/* -------------------------------------------------------------------------- */

int main(void) {
  char root[512];
#ifdef _WIN32
  const char *base = getenv("TEMP");
  if (base == NULL) base = ".";
  snprintf(root, sizeof(root), "%s\\md-lock-threads-%u", base, (unsigned)GetCurrentProcessId());
  _mkdir(root);
#else
  snprintf(root, sizeof(root), "/tmp/md-lock-threads-%u", (unsigned)getpid());
  mkdir(root, 0700);
#endif

  printf("table maintenance lock, same process, real threads\n");
  printf("root: %s\n", root);

  case_exclusive_serializes(root);
  case_busy_is_visible(root);
  case_shared_coexists(root);
  case_no_slot_leak(root);
  case_writer_then_recoverer(root);

  /* The lock file must still be the same fixed file: nothing above may unlink
   * or replace it, or two writers would end up locking different inodes. */
  char lock_path[600];
  snprintf(lock_path, sizeof(lock_path), "%s/.moondelta.lock", root);
  FILE *probe = fopen(lock_path, "rb");
  check(probe != NULL, "the fixed lock file still exists at the documented path",
        "the lock file is missing: the lock could have been replaced or unlinked");
  if (probe != NULL) fclose(probe);

  printf("\n");
  if (failures != 0) {
    printf("LOCK THREADS: %d failure(s)\n", failures);
    return 1;
  }
  printf("LOCK THREADS: PASS (5 contention cases, same process)\n");
  return 0;
}
