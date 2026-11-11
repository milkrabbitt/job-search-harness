"""Small nonblocking process locks for POSIX and native Windows.

Lock files must remain open for the lifetime of the lock. Never unlink a lock
file while another process may be using it: a replacement inode would permit
two independent locks. Windows locks the first byte; POSIX locks the file.
"""

import errno
import os


_IS_WINDOWS = os.name == "nt"


def acquire_lock(file_obj):
    """Acquire an exclusive lock or raise BlockingIOError on contention.

    The caller opens the file in append/update mode so an empty Windows lock
    file can receive one byte. Both text and binary file objects are accepted.
    """
    if not _IS_WINDOWS:
        import fcntl

        fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return

    import msvcrt

    file_obj.seek(0, os.SEEK_END)
    if file_obj.tell() == 0:
        file_obj.write(b"\0" if "b" in getattr(file_obj, "mode", "") else "\0")
        file_obj.flush()
    file_obj.seek(0)
    try:
        msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise BlockingIOError(errno.EWOULDBLOCK, "Another process holds the lock") from None
        raise


def release_lock(file_obj):
    """Release a successfully acquired lock before closing its file."""
    if not _IS_WINDOWS:
        import fcntl

        fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
        return

    import msvcrt

    file_obj.seek(0)
    msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
