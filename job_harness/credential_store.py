"""Observe macOS login-keychain lock state without requesting unlock."""
import ctypes
import sys


def default_keychain_locked():
    if sys.platform != "darwin":
        return None
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        copy_default = security.SecKeychainCopyDefault
        get_status = security.SecKeychainGetStatus
        release = foundation.CFRelease
        copy_default.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        copy_default.restype = ctypes.c_int32
        get_status.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_status.restype = ctypes.c_int32
        release.argtypes = [ctypes.c_void_p]
        release.restype = None
    except (AttributeError, OSError):
        return None
    keychain = ctypes.c_void_p()
    if copy_default(ctypes.byref(keychain)) or not keychain:
        return None
    try:
        status = ctypes.c_uint32()
        if get_status(keychain, ctypes.byref(status)):
            return None
        return not bool(status.value & 1)  # kSecUnlockStateStatus
    finally:
        release(keychain)
