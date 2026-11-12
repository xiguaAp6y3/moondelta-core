#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moonbit.h"

/* S5 power-loss injection points; the implementation lives in
 * powerloss_probe.c. Inactive unless MOONDELTA_POWERLOSS_AT is set. */
int moondelta_powerloss_boundary(const char *name, const char *detail);

static const char *moondelta_basename(const char *path) {
  if (path == NULL) return "-";
  const char *slash = strrchr(path, '/');
#ifdef _WIN32
  const char *backslash = strrchr(path, '\\');
  if (backslash != NULL && (slash == NULL || backslash > slash)) {
    slash = backslash;
  }
#endif
  return slash == NULL ? path : slash + 1;
}

/* Tag a durable-state boundary with "<phase>-<operation>" plus the file it
 * concerns, so the host log identifies which commit step was cut. */
static void moondelta_boundary(const char *phase, const char *operation,
                               const char *path) {
  char name[96];
  if (phase == NULL) phase = "unknown";
  snprintf(name, sizeof(name), "%s-%s", phase, operation);
  (void)moondelta_powerloss_boundary(name, moondelta_basename(path));
}

#ifdef _WIN32
#include <windows.h>
#include <wchar.h>

static wchar_t *moondelta_wide(const uint8_t *utf8) {
  int needed = MultiByteToWideChar(CP_UTF8, 0, (const char *)utf8, -1, NULL, 0);
  if (needed <= 0) return NULL;
  wchar_t *wide = (wchar_t *)malloc((size_t)needed * sizeof(wchar_t));
  if (wide == NULL) return NULL;
  if (MultiByteToWideChar(CP_UTF8, 0, (const char *)utf8, -1, wide, needed) <= 0) {
    free(wide);
    return NULL;
  }
  return wide;
}

/* Open the directory that contains `path` for metadata flushes. Best effort:
 * Windows has no documented equivalent of Linux directory fsync, so failures
 * here must not be reported as POSIX-style durability confirmations. */
static void moondelta_flush_parent(const wchar_t *path) {
  wchar_t *parent = _wcsdup(path);
  if (parent == NULL) return;
  wchar_t *slash = wcsrchr(parent, L'\\');
  wchar_t *forward = wcsrchr(parent, L'/');
  if (forward != NULL && (slash == NULL || forward > slash)) slash = forward;
  if (slash == NULL) {
    parent[0] = L'.';
    parent[1] = L'\0';
  } else if (slash == parent) {
    slash[1] = L'\0';
  } else {
    *slash = L'\0';
  }
  HANDLE directory = CreateFileW(parent, GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_READ | FILE_SHARE_WRITE |
                                     FILE_SHARE_DELETE,
                                 NULL, OPEN_EXISTING,
                                 FILE_FLAG_BACKUP_SEMANTICS, NULL);
  if (directory != INVALID_HANDLE_VALUE) {
    FlushFileBuffers(directory);
    CloseHandle(directory);
  }
  free(parent);
}

MOONBIT_FFI_EXPORT int moondelta_atomic_write(moonbit_string_t path,
                                              moonbit_bytes_t content,
                                              int length) {
  size_t path_len = wcslen((const wchar_t *)path);
  wchar_t *directory = (wchar_t *)malloc((path_len + 1) * sizeof(wchar_t));
  if (directory == NULL) return ERROR_NOT_ENOUGH_MEMORY;
  memcpy(directory, path, (path_len + 1) * sizeof(wchar_t));
  wchar_t *slash = wcsrchr(directory, L'\\');
  wchar_t *forward = wcsrchr(directory, L'/');
  if (forward != NULL && (slash == NULL || forward > slash)) slash = forward;
  if (slash == NULL) {
    directory[0] = L'.';
    directory[1] = L'\0';
  } else if (slash == directory) {
    slash[1] = L'\0';
  } else {
    *slash = L'\0';
  }

  wchar_t temporary[MAX_PATH];
  if (GetTempFileNameW(directory, L"mdt", 0, temporary) == 0) {
    DWORD error = GetLastError();
    free(directory);
    return (int)error;
  }
  free(directory);

  HANDLE file = CreateFileW(temporary, GENERIC_WRITE, 0, NULL,
                            TRUNCATE_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
  if (file == INVALID_HANDLE_VALUE) {
    DWORD error = GetLastError();
    DeleteFileW(temporary);
    return (int)error;
  }

  int offset = 0;
  while (offset < length) {
    DWORD written = 0;
    DWORD chunk = (DWORD)((length - offset) > 0x7fffffff ? 0x7fffffff
                                                          : length - offset);
    if (!WriteFile(file, content + offset, chunk, &written, NULL)) {
      DWORD error = GetLastError();
      CloseHandle(file);
      DeleteFileW(temporary);
      return (int)error;
    }
    if (written == 0) {
      CloseHandle(file);
      DeleteFileW(temporary);
      return ERROR_WRITE_FAULT;
    }
    offset += (int)written;
  }
  if (!FlushFileBuffers(file)) {
    DWORD error = GetLastError();
    CloseHandle(file);
    DeleteFileW(temporary);
    return (int)error;
  }
  if (!CloseHandle(file)) {
    DWORD error = GetLastError();
    DeleteFileW(temporary);
    return (int)error;
  }
  if (!CreateHardLinkW(path, temporary, NULL)) {
    DWORD error = GetLastError();
    DeleteFileW(temporary);
    if (error == ERROR_ALREADY_EXISTS || error == ERROR_FILE_EXISTS) return -1;
    return (int)error;
  }
  DeleteFileW(temporary);
  return 0;
}

/* Transaction publication with the temporary name fixed by a persisted intent.
 * Windows keeps process-crash atomicity (write + FlushFileBuffers + hard link)
 * but makes no directory-entry durability claim. */
MOONBIT_FFI_EXPORT int moondelta_publish_pinned(moonbit_bytes_t temp,
                                                moonbit_bytes_t final_path,
                                                moonbit_bytes_t content,
                                                int length,
                                                moonbit_bytes_t phase_bytes) {
  (void)phase_bytes; /* Windows produces no power-loss evidence. */
  wchar_t *wide_temp = moondelta_wide(temp);
  wchar_t *wide_final = moondelta_wide(final_path);
  if (wide_temp == NULL || wide_final == NULL) {
    free(wide_temp);
    free(wide_final);
    return ENOMEM;
  }
  HANDLE file = CreateFileW(wide_temp, GENERIC_WRITE, 0, NULL, CREATE_NEW,
                            FILE_ATTRIBUTE_NORMAL, NULL);
  if (file == INVALID_HANDLE_VALUE) {
    DWORD error = GetLastError();
    free(wide_temp);
    free(wide_final);
    return (int)error;
  }
  int offset = 0;
  while (offset < length) {
    DWORD written = 0;
    DWORD chunk = (DWORD)((length - offset) > 0x7fffffff ? 0x7fffffff
                                                          : length - offset);
    if (!WriteFile(file, content + offset, chunk, &written, NULL)) {
      DWORD error = GetLastError();
      CloseHandle(file);
      free(wide_temp);
      free(wide_final);
      return (int)error;
    }
    if (written == 0) {
      CloseHandle(file);
      free(wide_temp);
      free(wide_final);
      return ERROR_WRITE_FAULT;
    }
    offset += (int)written;
  }
  if (!FlushFileBuffers(file)) {
    DWORD error = GetLastError();
    CloseHandle(file);
    free(wide_temp);
    free(wide_final);
    return (int)error;
  }
  if (!CloseHandle(file)) {
    DWORD error = GetLastError();
    free(wide_temp);
    free(wide_final);
    return (int)error;
  }
  if (!CreateHardLinkW(wide_final, wide_temp, NULL)) {
    DWORD error = GetLastError();
    free(wide_temp);
    free(wide_final);
    if (error == ERROR_ALREADY_EXISTS || error == ERROR_FILE_EXISTS) return -1;
    return (int)error;
  }
  free(wide_temp);
  free(wide_final);
  return 0;
}

MOONBIT_FFI_EXPORT int moondelta_sync_file(moonbit_bytes_t path) {
  wchar_t *wide = moondelta_wide(path);
  if (wide == NULL) return ENOMEM;
  HANDLE file = CreateFileW(wide, GENERIC_READ | GENERIC_WRITE,
                            FILE_SHARE_READ | FILE_SHARE_WRITE |
                                FILE_SHARE_DELETE,
                            NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
  if (file == INVALID_HANDLE_VALUE) {
    DWORD error = GetLastError();
    free(wide);
    return (int)error;
  }
  int result = 0;
  if (!FlushFileBuffers(file)) result = (int)GetLastError();
  CloseHandle(file);
  free(wide);
  return result;
}

MOONBIT_FFI_EXPORT int64_t moondelta_file_size(moonbit_bytes_t path) {
  wchar_t *wide = moondelta_wide(path);
  if (wide == NULL) return -ENOMEM;
  WIN32_FILE_ATTRIBUTE_DATA data;
  if (!GetFileAttributesExW(wide, GetFileExInfoStandard, &data)) {
    DWORD error = GetLastError();
    free(wide);
    return -(int64_t)(int)error;
  }
  free(wide);
  return ((int64_t)data.nFileSizeHigh << 32) | (int64_t)data.nFileSizeLow;
}

MOONBIT_FFI_EXPORT int moondelta_is_symlink(moonbit_bytes_t path) {
  wchar_t *wide = moondelta_wide(path);
  if (wide == NULL) return -ENOMEM;
  DWORD attributes = GetFileAttributesW(wide);
  DWORD error = GetLastError();
  free(wide);
  if (attributes == INVALID_FILE_ATTRIBUTES) return (int)error == 0 ? -ENOENT
                                                                   : -(int)error;
  return (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 ? 1 : 0;
}

MOONBIT_FFI_EXPORT int moondelta_unlink_sync(moonbit_bytes_t path) {
  wchar_t *wide = moondelta_wide(path);
  if (wide == NULL) return -ENOMEM;
  if (!DeleteFileW(wide)) {
    DWORD error = GetLastError();
    free(wide);
    if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) return 1;
    return -(int)error;
  }
  moondelta_flush_parent(wide);
  free(wide);
  return 0;
}

/* System CSPRNG without link-time dependencies: ProcessPrng (bcryptprimitives)
 * first, then the legacy advapi32 SystemFunction036 export. */
typedef BOOL(WINAPI *md_process_prng_fn)(PBYTE, SIZE_T);
typedef BOOLEAN(WINAPI *md_rtl_gen_random_fn)(PVOID, ULONG);

MOONBIT_FFI_EXPORT int moondelta_random_hex(moonbit_bytes_t out,
                                            int byte_length) {
  if (byte_length <= 0) return EINVAL;
  uint8_t *raw = (uint8_t *)malloc((size_t)byte_length);
  if (raw == NULL) return ENOMEM;
  int ok = 0;
  HMODULE module = LoadLibraryW(L"bcryptprimitives.dll");
  if (module != NULL) {
    md_process_prng_fn process_prng =
        (md_process_prng_fn)(void *)GetProcAddress(module, "ProcessPrng");
    if (process_prng != NULL && process_prng(raw, (SIZE_T)byte_length)) ok = 1;
    FreeLibrary(module);
  }
  if (!ok) {
    module = LoadLibraryW(L"advapi32.dll");
    if (module != NULL) {
      md_rtl_gen_random_fn gen_random = (md_rtl_gen_random_fn)(void *)
          GetProcAddress(module, "SystemFunction036");
      if (gen_random != NULL && gen_random(raw, (ULONG)byte_length)) ok = 1;
      FreeLibrary(module);
    }
  }
  if (!ok) {
    free(raw);
    return EIO;
  }
  static const char digits[] = "0123456789abcdef";
  for (int i = 0; i < byte_length; i++) {
    out[i * 2] = (uint8_t)digits[raw[i] >> 4];
    out[i * 2 + 1] = (uint8_t)digits[raw[i] & 0x0f];
  }
  memset(raw, 0, (size_t)byte_length);
  free(raw);
  return 0;
}

/* Windows byte-range locks. Handles are stored in a small slot table so the
 * MoonBit Int never has to carry a 64-bit HANDLE. CreateFileW is called with a
 * NULL security descriptor, so the handle is not inheritable by child
 * processes; the OS releases the lock when the process exits. */
#define MD_LOCK_SLOTS 64
static HANDLE md_lock_handles[MD_LOCK_SLOTS];
static volatile LONG md_lock_used[MD_LOCK_SLOTS];

MOONBIT_FFI_EXPORT int moondelta_lock_table(moonbit_bytes_t root, int mode) {
  wchar_t *wide_root = moondelta_wide(root);
  if (wide_root == NULL) return -ENOMEM;
  size_t root_len = wcslen(wide_root);
  static const wchar_t suffix[] = L"/.moondelta.lock";
  wchar_t *path = (wchar_t *)malloc((root_len + sizeof(suffix) / sizeof(wchar_t)) *
                                    sizeof(wchar_t));
  if (path == NULL) {
    free(wide_root);
    return -ENOMEM;
  }
  memcpy(path, wide_root, root_len * sizeof(wchar_t));
  memcpy(path + root_len, suffix, sizeof(suffix));
  free(wide_root);
  /* The lock file is persistent: never unlink or replace it. Opening with
   * FILE_SHARE_* lets concurrent writers and the recoverer coordinate. */
  HANDLE handle = CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                              FILE_SHARE_READ | FILE_SHARE_WRITE,
                              NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
  free(path);
  if (handle == INVALID_HANDLE_VALUE) return -(int)GetLastError();
  OVERLAPPED overlapped;
  memset(&overlapped, 0, sizeof(overlapped));
  DWORD flags = 0;
  if (mode >= 1) flags |= LOCKFILE_EXCLUSIVE_LOCK;
  if (mode == 2) flags |= LOCKFILE_FAIL_IMMEDIATELY;
  if (!LockFileEx(handle, flags, 0, 1, 0, &overlapped)) {
    DWORD error = GetLastError();
    CloseHandle(handle);
    if (error == ERROR_LOCK_VIOLATION) return -11;
    return -(int)error;
  }
  for (int slot = 0; slot < MD_LOCK_SLOTS; slot++) {
    if (InterlockedCompareExchange(&md_lock_used[slot], 1, 0) == 0) {
      md_lock_handles[slot] = handle;
      return slot + 1;
    }
  }
  UnlockFileEx(handle, 0, 1, 0, &overlapped);
  CloseHandle(handle);
  return -EMFILE;
}

MOONBIT_FFI_EXPORT int moondelta_unlock_table(int handle_index) {
  if (handle_index <= 0 || handle_index > MD_LOCK_SLOTS) return EINVAL;
  int slot = handle_index - 1;
  if (md_lock_used[slot] == 0) return EINVAL;
  HANDLE handle = md_lock_handles[slot];
  OVERLAPPED overlapped;
  memset(&overlapped, 0, sizeof(overlapped));
  int result = 0;
  if (!UnlockFileEx(handle, 0, 1, 0, &overlapped)) result = (int)GetLastError();
  if (!CloseHandle(handle) && result == 0) result = (int)GetLastError();
  md_lock_handles[slot] = NULL;
  InterlockedExchange(&md_lock_used[slot], 0);
  return result;
}

#else
#include <fcntl.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

/* mode: 0 = shared blocking, 1 = exclusive blocking, 2 = exclusive non-blocking.
 * The fixed lock file is never unlinked or replaced, and O_CLOEXEC keeps the
 * descriptor out of child processes. */
MOONBIT_FFI_EXPORT int moondelta_lock_table(moonbit_bytes_t root, int mode) {
  size_t root_len = strlen((const char *)root);
  static const char suffix[] = "/.moondelta.lock";
  char *path = (char *)malloc(root_len + sizeof(suffix));
  if (path == NULL) return -ENOMEM;
  memcpy(path, root, root_len);
  memcpy(path + root_len, suffix, sizeof(suffix));
  int fd = open(path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
  int error = errno;
  free(path);
  if (fd < 0) return -error;
  struct stat info;
  if (fstat(fd, &info) != 0) {
    error = errno;
    close(fd);
    return -error;
  }
  if (!S_ISREG(info.st_mode)) {
    close(fd);
    return -EINVAL;
  }
  int operation;
  if (mode == 2) operation = LOCK_EX | LOCK_NB;
  else if (mode == 1) operation = LOCK_EX;
  else operation = LOCK_SH;
  while (flock(fd, operation) != 0) {
    if (errno == EINTR) continue;
    error = errno;
    close(fd);
    return -error;
  }
  return fd;
}

MOONBIT_FFI_EXPORT int moondelta_unlock_table(int fd) {
  int error = 0;
  if (flock(fd, LOCK_UN) != 0) error = errno;
  if (close(fd) != 0 && error == 0) error = errno;
  return error;
}

/* Open the actual parent before publishing; the descriptor stays pinned while
 * link/unlink and directory synchronization run. */
static int open_parent(const char *path) {
  char *parent = strdup(path);
  if (parent == NULL) { errno = ENOMEM; return -1; }
  char *slash = strrchr(parent, '/');
  if (slash == NULL) {
    free(parent);
    return open(".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  }
  if (slash == parent) slash[1] = '\0';
  else *slash = '\0';
  int fd = open(parent, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  int error = errno;
  free(parent);
  errno = error;
  return fd;
}

static int sync_fd(int fd) {
  int result;
  do { result = fsync(fd); } while (result != 0 && errno == EINTR);
  return result;
}

MOONBIT_FFI_EXPORT int moondelta_sync_parent(moonbit_bytes_t path) {
  int fd = open_parent((const char *)path);
  if (fd < 0) return errno;
  int error = sync_fd(fd) == 0 ? 0 : errno;
  if (close(fd) != 0 && error == 0) error = errno;
  return error;
}

MOONBIT_FFI_EXPORT int moondelta_sync_file(moonbit_bytes_t path) {
  int fd = open((const char *)path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0) return errno;
  int error = sync_fd(fd) == 0 ? 0 : errno;
  if (close(fd) != 0 && error == 0) error = errno;
  return error;
}

MOONBIT_FFI_EXPORT int64_t moondelta_file_size(moonbit_bytes_t path) {
  int fd = open((const char *)path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0) return -(int64_t)errno;
  struct stat info;
  if (fstat(fd, &info) != 0) {
    int error = errno;
    close(fd);
    return -(int64_t)error;
  }
  close(fd);
  return (int64_t)info.st_size;
}

MOONBIT_FFI_EXPORT int moondelta_is_symlink(moonbit_bytes_t path) {
  struct stat info;
  if (lstat((const char *)path, &info) != 0) {
    if (errno == ENOENT) return 0;
    return -errno;
  }
  return S_ISLNK(info.st_mode) ? 1 : 0;
}

MOONBIT_FFI_EXPORT int moondelta_unlink_sync(moonbit_bytes_t path) {
  if (unlink((const char *)path) != 0) {
    if (errno == ENOENT) return 1;
    return -errno;
  }
  /* Removed from the namespace, but the deletion is not durable yet. */
  moondelta_boundary("cleanup", "unlinked", (const char *)path);
  int directory = open_parent((const char *)path);
  if (directory < 0) return -errno;
  int error = sync_fd(directory) == 0 ? 0 : -errno;
  if (close(directory) != 0 && error == 0) error = -errno;
  if (error == 0) moondelta_boundary("cleanup", "dir-fsynced", (const char *)path);
  return error;
}

/* Linux getrandom(2) first; /dev/urandom only if the syscall is unavailable. */
MOONBIT_FFI_EXPORT int moondelta_random_hex(moonbit_bytes_t out,
                                            int byte_length) {
  if (byte_length <= 0) return EINVAL;
  uint8_t *raw = (uint8_t *)malloc((size_t)byte_length);
  if (raw == NULL) return ENOMEM;
  size_t offset = 0;
#if defined(__linux__)
  while (offset < (size_t)byte_length) {
    ssize_t got = getrandom(raw + offset, (size_t)byte_length - offset, 0);
    if (got < 0 && errno == EINTR) continue;
    if (got <= 0) break;
    offset += (size_t)got;
  }
#endif
  if (offset < (size_t)byte_length) {
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd < 0) { free(raw); return errno; }
    while (offset < (size_t)byte_length) {
      ssize_t got = read(fd, raw + offset, (size_t)byte_length - offset);
      if (got < 0 && errno == EINTR) continue;
      if (got <= 0) {
        int error = got == 0 ? EIO : errno;
        close(fd);
        free(raw);
        return error;
      }
      offset += (size_t)got;
    }
    close(fd);
  }
  static const char digits[] = "0123456789abcdef";
  for (int i = 0; i < byte_length; i++) {
    out[i * 2] = (uint8_t)digits[raw[i] >> 4];
    out[i * 2 + 1] = (uint8_t)digits[raw[i] & 0x0f];
  }
  memset(raw, 0, (size_t)byte_length);
  free(raw);
  return 0;
}

/* Publish with an intent-pinned temporary name. Return codes:
 *   0      visible and (POSIX) directory-synced; temporary name retained
 *   -1     final name already exists
 *   <=-2   final name visible but directory fsync failed: -(errno + 1)
 *   >0     prepublish failure (errno) */
MOONBIT_FFI_EXPORT int moondelta_publish_pinned(moonbit_bytes_t temp,
                                                moonbit_bytes_t final_path,
                                                moonbit_bytes_t content,
                                                int length,
                                                moonbit_bytes_t phase_bytes) {
  const char *temporary = (const char *)temp;
  const char *final_name = (const char *)final_path;
  const char *phase = (const char *)phase_bytes;
  int directory_fd = open_parent(final_name);
  if (directory_fd < 0) return errno;
  int fd = open(temporary, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC | O_NOFOLLOW,
                0600);
  if (fd < 0) {
    int error = errno;
    close(directory_fd);
    return error;
  }
  moondelta_boundary(phase, "temp-created", final_name);
  int offset = 0;
  while (offset < length) {
    ssize_t written = write(fd, content + offset, (size_t)(length - offset));
    if (written < 0 && errno == EINTR) continue;
    if (written < 0) {
      int error = errno;
      close(fd);
      close(directory_fd);
      return error;
    }
    if (written == 0) {
      close(fd);
      close(directory_fd);
      return EIO;
    }
    offset += (int)written;
  }
  /* Full content handed to the page cache, nothing durable yet. */
  moondelta_boundary(phase, "written", final_name);
  if (sync_fd(fd) != 0) {
    int error = errno;
    close(fd);
    close(directory_fd);
    return error;
  }
  moondelta_boundary(phase, "fsynced", final_name);
  if (close(fd) != 0) {
    int error = errno;
    close(directory_fd);
    return error;
  }
  if (link(temporary, final_name) != 0) {
    int error = errno;
    close(directory_fd);
    if (error == EEXIST) return -1;
    return error;
  }
  /* Name visible; its directory entry is not durable until the sync below. */
  moondelta_boundary(phase, "linked", final_name);
  if (sync_fd(directory_fd) != 0) {
    int error = errno;
    close(directory_fd);
    return -(error + 1);
  }
  moondelta_boundary(phase, "dir-fsynced", final_name);
  close(directory_fd);
  return 0;
}

MOONBIT_FFI_EXPORT int moondelta_atomic_write(moonbit_bytes_t path,
                                              moonbit_bytes_t content,
                                              int length) {
  size_t path_len = strlen((const char *)path);
  static const char suffix[] = ".moondelta-tmp-XXXXXX";
  char *temporary = (char *)malloc(path_len + sizeof(suffix));
  if (temporary == NULL) return ENOMEM;
  memcpy(temporary, path, path_len);
  memcpy(temporary + path_len, suffix, sizeof(suffix));

  int directory_fd = open_parent((const char *)path);
  if (directory_fd < 0) {
    int error = errno;
    free(temporary);
    return error;
  }
  int fd = mkstemp(temporary);
  if (fd < 0) {
    int error = errno;
    close(directory_fd);
    free(temporary);
    return error;
  }
  int offset = 0;
  while (offset < length) {
    ssize_t written = write(fd, content + offset, (size_t)(length - offset));
    if (written < 0 && errno == EINTR) continue;
    if (written < 0) {
      int error = errno;
      close(fd);
      unlink(temporary);
      close(directory_fd);
      free(temporary);
      return error;
    }
    if (written == 0) {
      close(fd);
      unlink(temporary);
      close(directory_fd);
      free(temporary);
      return EIO;
    }
    offset += (int)written;
  }
  if (sync_fd(fd) != 0) {
    int error = errno;
    close(fd);
    unlink(temporary);
    close(directory_fd);
    free(temporary);
    return error;
  }
  if (close(fd) != 0) {
    int error = errno;
    unlink(temporary);
    close(directory_fd);
    free(temporary);
    return error;
  }
  if (link(temporary, (const char *)path) != 0) {
    int error = errno;
    unlink(temporary);
    close(directory_fd);
    free(temporary);
    if (error == EEXIST) return -1;
    return error;
  }
  /* A negative result below -1 means the final name is already visible.
   * Preserve BOTH names if its directory flush fails: never roll back a
   * visible commit, and never report this as a safe-to-retry prepublish error. */
  if (sync_fd(directory_fd) != 0) {
    int error = errno;
    close(directory_fd);
    free(temporary);
    return -(error + 1);
  }
  /* Cleanup happens after the publication durability boundary. A failure
   * here must not turn a durable commit into an apparent failed append. */
  if (unlink(temporary) == 0) (void)sync_fd(directory_fd);
  close(directory_fd);
  free(temporary);
  return 0;
}
#endif
