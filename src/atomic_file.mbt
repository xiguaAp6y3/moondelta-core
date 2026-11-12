///|
/// 平台维护锁忙错误码（POSIX `EWOULDBLOCK` 与 Windows `LOCKFILE_FAIL_IMMEDIATELY`
/// 都在 C 层归一化为 `-11`）。
const LOCK_BUSY_CODE : Int = 11

///|
/// 旧版“目标路径 + 随机临时名”的原子发布入口。保留给 create/append 之外
/// 的内部调用；事务写入路径使用 intent 预先固定的临时名（见 `publish_pinned_file`）。
#cfg(all(target="native", platform="windows"))
#borrow(path, content)
extern "C" fn atomic_write_ffi(
  path : String,
  content : Bytes,
  length : Int,
) -> Int = "moondelta_atomic_write"

///|
#cfg(all(target="native", not(platform="windows")))
#borrow(path, content)
extern "C" fn atomic_write_ffi(
  path : Bytes,
  content : Bytes,
  length : Int,
) -> Int = "moondelta_atomic_write"

///|
#cfg(all(target="native", not(platform="windows")))
#borrow(path)
extern "C" fn sync_parent_ffi(path : Bytes) -> Int = "moondelta_sync_parent"

///|
/// 以 intent 预先记录的临时路径发布：排他创建临时文件 → 写完整 → 文件 fsync
/// → 不覆盖 `link` 到正式路径 → 父目录 fsync。临时名保留到调用方清理阶段。
/// POSIX 返回 0/正 errno/-1(目标已存在)/<=-2(已可见但目录同步失败 = -(errno+1))；
/// Windows 保留进程崩溃原子发布语义，不做目录项持久性声明。
///
/// `phase` 只用于 S5 掉电注入探针（`intent`/`data`/`commit`），把每个系统调用
/// 边界标记成 `<phase>-fsynced` 等名称；未设置 `MOONDELTA_POWERLOSS_AT` 时
/// 探针完全空转，不影响正常提交路径。
#cfg(target="native")
#borrow(temp, final_path, content, phase)
extern "C" fn publish_pinned_ffi(
  temp : Bytes,
  final_path : Bytes,
  content : Bytes,
  length : Int,
  phase : Bytes,
) -> Int = "moondelta_publish_pinned"

///|
#cfg(target="native")
#borrow(path)
extern "C" fn sync_file_ffi(path : Bytes) -> Int = "moondelta_sync_file"

///|
#cfg(target="native")
#borrow(path)
extern "C" fn file_size_ffi(path : Bytes) -> Int64 = "moondelta_file_size"

///|
#cfg(target="native")
#borrow(path)
extern "C" fn is_symlink_ffi(path : Bytes) -> Int = "moondelta_is_symlink"

///|
/// unlink 并同步父目录；返回 0 成功、1 文件不存在（幂等）、其余为 -errno。
#cfg(target="native")
#borrow(path)
extern "C" fn unlink_sync_ffi(path : Bytes) -> Int = "moondelta_unlink_sync"

///|
#cfg(target="native")
#borrow(out)
extern "C" fn random_hex_ffi(out : Bytes, byte_length : Int) -> Int =
  "moondelta_random_hex"

///|
/// 表级维护锁。mode：0=共享阻塞，1=独占阻塞，2=独占非阻塞。
#cfg(target="native")
#borrow(root)
extern "C" fn lock_table_ffi(root : Bytes, mode : Int) -> Int =
  "moondelta_lock_table"

///|
#cfg(target="native")
extern "C" fn unlock_table_ffi(handle : Int) -> Int = "moondelta_unlock_table"

///|
fn c_path(path : String) -> Bytes {
  @utf8.encode(path) + Bytes::make(1, 0)
}

///|
#cfg(all(target="native", platform="windows"))
fn atomic_write_file(path : String, content : Bytes) -> Unit raise DeltaError {
  let result = atomic_write_ffi(path, content, content.length())
  if result != 0 {
    if result == -1 {
      raise DeltaError::AlreadyExists(path)
    }
    raise DeltaError::IoError(
      "atomic publish failed for '\{path}' (platform error \{result})",
    )
  }
}

///|
#cfg(all(target="native", not(platform="windows")))
fn atomic_write_file(path : String, content : Bytes) -> Unit raise DeltaError {
  let result = atomic_write_ffi(c_path(path), content, content.length())
  if result != 0 {
    if result == -1 {
      raise DeltaError::AlreadyExists(path)
    }
    if result < -1 {
      raise DeltaError::PublishOutcomeUnknown(
        "", -1, path, "legacy-publish-directory-sync", -result - 1,
      )
    }
    raise DeltaError::IoError(
      "atomic publish failed for '\{path}' (platform error \{result})",
    )
  }
}

///|
#cfg(not(any(target="native", target="llvm")))
fn atomic_write_file(_path : String, _content : Bytes) -> Unit raise DeltaError {
  raise DeltaError::Unsupported(
    "atomic Delta writes currently require the native filesystem backend",
  )
}

///|
/// 发布一个内容已完整构造的文件到预先确定的临时名与正式名。
/// `commit` 决定“已可见但未确认持久”应报成 commit 还是 publish 结果未知。
#cfg(target="native")
fn publish_pinned_file(
  temp_path : String,
  final_path : String,
  content : Bytes,
  txn_id : String,
  version : Int32,
  phase : String,
  commit : Bool,
) -> Unit raise DeltaError {
  let result = publish_pinned_ffi(
    c_path(temp_path),
    c_path(final_path),
    content,
    content.length(),
    c_path(phase),
  )
  if result == 0 {
    return
  }
  if result == -1 {
    raise DeltaError::AlreadyExists(final_path)
  }
  if result < -1 {
    let code = -result - 1
    if commit {
      raise DeltaError::CommitOutcomeUnknown(
        txn_id, version, final_path, phase, code,
      )
    } else {
      raise DeltaError::PublishOutcomeUnknown(
        txn_id, version, final_path, phase, code,
      )
    }
  }
  raise DeltaError::IoError(
    "pinned publish failed for '\{final_path}' (platform error \{result})",
  )
}

///|
#cfg(not(target="native"))
fn publish_pinned_file(
  _temp_path : String,
  _final_path : String,
  _content : Bytes,
  _txn_id : String,
  _version : Int32,
  _phase : String,
  _commit : Bool,
) -> Unit raise DeltaError {
  raise DeltaError::Unsupported(
    "pinned transaction writes currently require the native filesystem backend",
  )
}

///|
#cfg(target="native")
fn sync_file_checked(path : String) -> Unit raise DeltaError {
  let result = sync_file_ffi(c_path(path))
  if result != 0 {
    raise DeltaError::IoError(
      "file sync failed for '\{path}' (platform error \{result})",
    )
  }
}

///|
#cfg(not(target="native"))
fn sync_file_checked(_path : String) -> Unit raise DeltaError {
  ()
}

///|
#cfg(target="native")
fn file_size_checked(path : String) -> Int64 raise DeltaError {
  let result = file_size_ffi(c_path(path))
  if result < 0L {
    raise DeltaError::IoError(
      "cannot stat '\{path}' (platform error \{(-result).to_string()})",
    )
  }
  result
}

///|
#cfg(not(target="native"))
fn file_size_checked(_path : String) -> Int64 raise DeltaError {
  0L
}

///|
#cfg(target="native")
fn is_symlink_checked(path : String) -> Bool raise DeltaError {
  let result = is_symlink_ffi(c_path(path))
  if result < 0 {
    raise DeltaError::IoError(
      "cannot inspect '\{path}' (platform error \{(-result).to_string()})",
    )
  }
  result == 1
}

///|
#cfg(not(target="native"))
fn is_symlink_checked(_path : String) -> Bool raise DeltaError {
  false
}

///|
/// unlink 并同步父目录；文件已不存在视作幂等成功。返回是否真的删除了文件。
#cfg(target="native")
fn unlink_synced(path : String) -> Bool raise DeltaError {
  let result = unlink_sync_ffi(c_path(path))
  if result == 0 {
    return true
  }
  if result == 1 {
    return false
  }
  raise DeltaError::IoError(
    "cannot remove '\{path}' (platform error \{(-result).to_string()})",
  )
}

///|
#cfg(not(target="native"))
fn unlink_synced(_path : String) -> Bool raise DeltaError {
  false
}

///|
/// 操作系统加密随机源的 32 位十六进制事务 ID（16 字节熵）。
/// 不使用时间戳、PID、计数器或文件名碰撞概率作为唯一性证明。
#cfg(target="native")
fn random_txn_id() -> String raise DeltaError {
  let out = Bytes::make(32, 0)
  let result = random_hex_ffi(out, 16)
  if result != 0 {
    raise DeltaError::Unsupported(
      "cannot obtain an operating-system random transaction id (platform error \{result.to_string()})",
    )
  }
  @utf8.decode(out[:]) catch {
    _ =>
      raise DeltaError::Unsupported(
        "random source produced bytes that are not lowercase hex",
      )
  }
}

///|
#cfg(not(target="native"))
fn random_txn_id() -> String raise DeltaError {
  raise DeltaError::Unsupported(
    "transaction ids require the native platform random source",
  )
}

///|
#cfg(target="native")
fn acquire_table_lock(root : String, mode : Int) -> Int raise DeltaError {
  let _ = ensure_dir(root)
  let handle = lock_table_ffi(c_path(root), mode)
  if handle < 0 {
    raise DeltaError::IoError(
      "cannot acquire table maintenance lock under '\{root}' (platform error \{(-handle).to_string()})",
    )
  }
  handle
}

///|
/// 取得 writer 共享锁（阻塞）。多个 writer 仍通过 put-if-absent 竞争版本。
#cfg(target="native")
fn acquire_shared_lock(root : String) -> Int raise DeltaError {
  acquire_table_lock(root, 0)
}

///|
/// 尝试一次非阻塞独占锁；锁忙返回 None，不等待。
#cfg(target="native")
fn try_acquire_exclusive_lock(root : String) -> Int? raise DeltaError {
  let handle = lock_table_ffi(c_path(root), 2)
  if handle == -LOCK_BUSY_CODE {
    return None
  }
  if handle < 0 {
    raise DeltaError::IoError(
      "cannot acquire exclusive maintenance lock under '\{root}' (platform error \{(-handle).to_string()})",
    )
  }
  Some(handle)
}

///|
#cfg(target="native")
fn acquire_exclusive_lock(root : String) -> Int raise DeltaError {
  acquire_table_lock(root, 1)
}

///|
#cfg(target="native")
fn release_table_lock(handle : Int) -> Unit {
  ignore(unlock_table_ffi(handle))
}

///|
#cfg(not(target="native"))
fn acquire_shared_lock(_root : String) -> Int raise DeltaError {
  raise DeltaError::Unsupported(
    "table maintenance locks require the native filesystem backend",
  )
}

///|
#cfg(not(target="native"))
fn try_acquire_exclusive_lock(_root : String) -> Int? raise DeltaError {
  raise DeltaError::Unsupported(
    "table maintenance locks require the native filesystem backend",
  )
}

///|
#cfg(not(target="native"))
fn acquire_exclusive_lock(_root : String) -> Int raise DeltaError {
  raise DeltaError::Unsupported(
    "table maintenance locks require the native filesystem backend",
  )
}

///|
#cfg(not(target="native"))
fn release_table_lock(_handle : Int) -> Unit {
  ()
}

///|
#cfg(all(target="native", not(platform="windows")))
fn sync_directory_entry(path : String) -> Unit raise DeltaError {
  let result = sync_parent_ffi(c_path(path))
  if result != 0 {
    raise DeltaError::IoError(
      "directory sync failed for '\{path}' (platform error \{result.to_string()})",
    )
  }
}

///|
#cfg(any(not(target="native"), platform="windows"))
fn sync_directory_entry(_path : String) -> Unit raise DeltaError {
  // Windows keeps its existing process-crash guarantee; no directory durability claim.
}
